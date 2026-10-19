import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from freqtrade.forex.api import create_app
from freqtrade.forex.auto_execution import (
    AutoExecutionError,
    OandaAutoStrategyExecutor,
)
from freqtrade.forex.config import OandaSettings
from freqtrade.forex.models import (
    OandaCandle,
    OandaEnvironment,
    OandaHomeConversion,
    OandaInstrument,
    OandaOrderResult,
    OandaPrice,
    OandaPricing,
)
from freqtrade.forex.oanda import OandaAPIError, OandaClient
from freqtrade.forex.state import OandaAccountState


def _auth_headers(client, role: str = "operator") -> dict[str, str]:
    passwords = {
        "viewer": "test-viewer-password",
        "operator": "test-operator-password",
        "admin": "test-admin-password",
    }
    response = client.post(
        "/api/v1/auth/login",
        json={"username": role, "password": passwords[role]},
    )
    assert response.status_code == 200, response.text
    session = response.json()
    return {
        "X-Session-Token": session["sessionToken"],
        "X-User-Role": session["user"]["role"],
        "X-CSRF-Token": session["csrfToken"],
    }


class FakeOandaClient:
    def __init__(self, candles: list[OandaCandle]) -> None:
        self.candles = candles
        self.orders: list[tuple[tuple, dict]] = []
        self.open_trades: list[dict] = []
        self.close_result: dict = {"orderFillTransaction": {"id": "close-1"}}
        self.closed_trade_ids: list[str] = []
        self.order_modifications: list[tuple[str, dict]] = []

    async def get_account_summary(self) -> OandaAccountState:
        return OandaAccountState(
            account_id="test-account",
            currency="USD",
            balance=Decimal(10000),
            nav=Decimal(10000),
            margin_available=Decimal(9000),
            unrealized_pl=Decimal(0),
        )

    async def get_open_trades(self) -> list[dict]:
        return self.open_trades

    async def close_trade(self, trade_id: str) -> dict:
        self.closed_trade_ids.append(trade_id)
        return self.close_result

    async def modify_trade_orders(self, trade_id: str, **kwargs) -> dict:
        self.order_modifications.append((trade_id, kwargs))
        return {"trailingStopLossOrderTransaction": {"id": "trailing-1"}}

    async def get_candles(self, instrument: str, granularity: str, *, count: int):
        del instrument, granularity, count
        return self.candles

    async def get_prices(self, instruments: tuple[str, ...]) -> list[OandaPrice]:
        if instruments != ("EUR_USD",):
            return []
        return [
            OandaPrice(
                instrument="EUR_USD",
                time="2026-10-03T18:00:00Z",
                bid=Decimal("1.1000"),
                ask=Decimal("1.1002"),
                bids=((Decimal("1.1000"), Decimal(4000)),),
                asks=((Decimal("1.1002"), Decimal(2500)),),
                units_available={
                    "default": {"long": "1500", "short": "1800"},
                },
            )
        ]

    async def get_instruments(self, instruments: tuple[str, ...]):
        assert instruments == ("EUR_USD",)
        return [
            OandaInstrument(
                name="EUR_USD",
                display_name="EUR/USD",
                pip_location=-4,
                display_precision=5,
                trade_units_precision=0,
                minimum_trade_size=Decimal(1),
                margin_rate=Decimal("0.02"),
            )
        ]

    async def create_market_order(self, *args, **kwargs) -> OandaOrderResult:
        self.orders.append((args, kwargs))
        return OandaOrderResult(
            order_id="order-1",
            transaction_id="transaction-1",
            fill_price=Decimal("1.1001"),
            units=Decimal(str(args[1])),
        )


def _strategy_directory(tmp_path, monkeypatch) -> None:
    directory = tmp_path / "strategies"
    directory.mkdir()
    (directory / "AutoTestStrategy.py").write_text(
        """from freqtrade.strategy import IStrategy

class AutoTestStrategy(IStrategy):
    timeframe = "5m"
    startup_candle_count = 2
    can_short = True
    entry_threshold = 0.0
    minimal_roi = {"0": 0.0005}
    stoploss = -0.50

    def populate_indicators(self, dataframe, metadata):
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        close = dataframe["close"]
        threshold = float(self.entry_threshold)
        dataframe["enter_long"] = (
            (close > close.shift(1) * (1 + threshold))
            & (close.shift(1) <= close.shift(2))
        )
        dataframe["enter_short"] = (
            (close < close.shift(1) * (1 - threshold))
            & (close.shift(1) >= close.shift(2))
        )
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = False
        dataframe["exit_short"] = False
        return dataframe
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("FOREX_STRATEGIES_DIR", str(directory))


def _candles(direction: str) -> list[OandaCandle]:
    closes = (
        [Decimal("1.0"), Decimal("1.0"), Decimal("1.1")]
        if direction == "long"
        else [Decimal("1.1"), Decimal("1.1"), Decimal("1.0")]
    )
    start = datetime(2026, 10, 3, 17, 45, tzinfo=UTC)
    return [
        OandaCandle(
            time=(start + timedelta(minutes=5 * index)).isoformat().replace("+00:00", "Z"),
            complete=True,
            open=value,
            high=value,
            low=value,
            close=value,
            volume=0,
        )
        for index, value in enumerate(closes)
    ]


def _executor(
    client: FakeOandaClient,
    *,
    risk_fraction: str = "0.01",
) -> OandaAutoStrategyExecutor:
    setup = {
        "pair_approved_revisions": {
            "EUR_USD": {
                "pair": "EUR/USD",
                "timeframe": "5m",
                "strategyClass": "AutoTestStrategy",
            }
        },
        "pair_strategies": {"EUR_USD": "AutoTestStrategy"},
        "pair_timeframes": {"EUR_USD": "5m"},
    }
    risk = {
        "EUR/USD": {
            "side": "BOTH",
            "maxExposure": "$10,000",
            "maxExposureMode": "absolute",
            "stopLoss": "10",
            "stopLossMode": "pips",
            "takeProfit": "20",
            "takeProfitMode": "pips",
        }
    }
    return OandaAutoStrategyExecutor(
        client,
        OandaSettings(
            "token",
            "test-account",
            instruments=("EUR_USD",),
            risk_fraction=risk_fraction,
            execution_mode="practice",
        ),
        setup,
        risk,
    )


@pytest.mark.asyncio
async def test_modified_oanda_trailing_stop_uses_instrument_precision_and_minimum() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "instruments": [
                        {
                            "name": "GBP_USD",
                            "displayName": "GBP/USD",
                            "pipLocation": -4,
                            "displayPrecision": 5,
                            "tradeUnitsPrecision": 0,
                            "minimumTradeSize": "1",
                            "minimumTrailingStopDistance": "0.000605",
                            "maximumTrailingStopDistance": "0.01",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={"trailingStopLossOrderTransaction": {"id": "trailing-1"}},
        )

    async with httpx.AsyncClient(
        base_url=OandaEnvironment.PRACTICE.rest_url,
        transport=httpx.MockTransport(handler),
    ) as http_client:
        async with OandaClient("ignored", "account", http_client=http_client) as client:
            await client.modify_trade_orders(
                "trade-1",
                trailing_stop_loss_distance="0.000602",
                instrument="GBP_USD",
            )

    assert len(requests) == 2
    assert requests[1].content.decode() == (
        '{"stopLoss":null,"trailingStopLoss":{"distance":"0.00061"}}'
    )


@pytest.mark.asyncio
async def test_quote_to_account_conversion_uses_oanda_home_conversion_factors():
    class ConversionClient:
        async def get_pricing(
            self,
            instruments: tuple[str, ...],
            *,
            include_home_conversions: bool,
        ):
            assert instruments == ("GBP_USD",)
            assert include_home_conversions is True
            return OandaPricing(
                prices=(),
                home_conversions={
                    "GBP": OandaHomeConversion(
                        currency="GBP",
                        account_gain=Decimal("1.24"),
                        account_loss=Decimal("1.26"),
                        position_value=Decimal("1.25"),
                    )
                }
            )

    executor = _executor(ConversionClient())

    conversion = await executor._quote_to_account_conversion(
        "GBP",
        "USD",
        preferred_instrument="GBP_USD",
    )

    assert conversion.account_gain == Decimal("1.24")
    assert conversion.account_loss == Decimal("1.26")
    assert conversion.position_value == Decimal("1.25")


@pytest.mark.asyncio
async def test_quote_to_account_conversion_discovers_supported_instrument_for_currency():
    class ConversionClient:
        async def get_instruments(self):
            return [
                OandaInstrument(
                    name="EUR_USD",
                    display_name="EUR/USD",
                    pip_location=-4,
                    display_precision=5,
                    trade_units_precision=0,
                    minimum_trade_size=Decimal(1),
                ),
                OandaInstrument(
                    name="GBP_USD",
                    display_name="GBP/USD",
                    pip_location=-4,
                    display_precision=5,
                    trade_units_precision=0,
                    minimum_trade_size=Decimal(1),
                ),
            ]

        async def get_pricing(
            self,
            instruments: tuple[str, ...],
            *,
            include_home_conversions: bool,
        ):
            assert instruments == ("GBP_USD",)
            assert include_home_conversions is True
            return OandaPricing(
                prices=(),
                home_conversions={
                    "GBP": OandaHomeConversion(
                        currency="GBP",
                        account_gain=Decimal("1.24"),
                        account_loss=Decimal("1.26"),
                        position_value=Decimal("1.25"),
                    )
                }
            )

    executor = _executor(ConversionClient())

    conversion = await executor._quote_to_account_conversion("GBP", "USD")

    assert conversion.position_value == Decimal("1.25")


@pytest.mark.asyncio
async def test_oanda_pricing_endpoint_returns_home_conversion_factors():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "prices": [],
                "homeConversions": [
                    {
                        "currency": "GBP",
                        "accountGain": "1.24",
                        "accountLoss": "1.26",
                        "positionValue": "1.25",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url=OandaEnvironment.PRACTICE.rest_url,
        transport=transport,
    ) as http_client:
        async with OandaClient("token", "account", http_client=http_client) as client:
            pricing = await client.get_pricing(
                ("GBP_USD",),
                include_home_conversions=True,
            )

    assert requests[0].url.params["instruments"] == "GBP_USD"
    assert requests[0].url.params["includeHomeConversions"] == "true"
    assert pricing.home_conversions["GBP"].account_gain == Decimal("1.24")
    assert pricing.home_conversions["GBP"].account_loss == Decimal("1.26")
    assert pricing.home_conversions["GBP"].position_value == Decimal("1.25")


@pytest.mark.asyncio
@pytest.mark.parametrize(("direction", "expected_units"), [("long", 1500), ("short", -1800)])
async def test_approved_long_and_short_signals_submit_signed_protected_oanda_orders(
    direction: str,
    expected_units: int,
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles(direction))
    executor = _executor(client)

    results = await executor.run_cycle()

    assert results[0]["signal"] == direction
    assert results[0]["status"] == "filled"
    assert client.orders[0][0] == ("EUR_USD", expected_units)
    assert client.orders[0][1]["stop_loss_price"] == ("1.0992" if direction == "long" else "1.1010")
    assert client.orders[0][1]["take_profit_price"] == (
        "1.1022" if direction == "long" else "1.0980"
    )
    assert client.orders[0][1]["trade_client_extensions"]["tag"] == "auto"


@pytest.mark.asyncio
async def test_approved_hyperopt_strategy_parameters_are_used_for_new_orders(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    executor = _executor(client)
    executor.setup["pair_approved_revisions"]["EUR_USD"]["hyperopt"] = {
        "parameters": {"entry_threshold": 0.2},
    }

    results = await executor.run_cycle()

    assert results[0]["signal"] == "flat"
    assert results[0]["status"] == "no_entry"
    assert client.orders == []


@pytest.mark.asyncio
async def test_setup_risk_fraction_sizes_orders_while_panel_stop_overrides_strategy_stoploss(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    executor = _executor(client, risk_fraction="0.0001")

    results = await executor.run_cycle()

    assert results[0]["status"] == "filled"
    assert client.orders[0][0] == ("EUR_USD", 1000)
    assert client.orders[0][1]["stop_loss_price"] == "1.0992"


@pytest.mark.asyncio
async def test_signal_candle_is_processed_at_most_once(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    executor = _executor(client)

    first = await executor.run_cycle()
    second = await executor.run_cycle()

    assert first[0]["status"] == "filled"
    assert second[0]["status"] == "already_processed"
    assert len(client.orders) == 1


@pytest.mark.asyncio
async def test_oanda_quote_error_is_reported_and_same_candle_can_retry(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    get_prices = client.get_prices
    calls = 0

    async def fail_once(instruments: tuple[str, ...]) -> list[OandaPrice]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OandaAPIError("OANDA 400: temporary pricing error")
        return await get_prices(instruments)

    client.get_prices = fail_once
    executor = _executor(client)

    first = await executor.run_cycle()
    assert executor.last_processed == {}
    second = await executor.run_cycle()

    assert first[0]["status"] == "blocked"
    assert first[0]["reason"] == "OANDA 400: temporary pricing error"
    assert second[0]["status"] == "filled"
    assert len(client.orders) == 1


@pytest.mark.asyncio
async def test_signal_is_blocked_when_oanda_does_not_return_depth(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    client.get_prices = lambda instruments: _no_depth_quote(instruments)
    executor = _executor(client)

    result = await executor.run_cycle()

    assert result[0]["status"] == "blocked"
    assert result[0]["signal"] == "long"
    assert result[0]["candleTime"] == datetime(2026, 10, 3, 17, 55, tzinfo=UTC).isoformat()
    assert "neither long quote liquidity nor available-unit limits" in str(result[0]["reason"])
    assert client.orders == []


@pytest.mark.asyncio
async def test_auto_execution_uses_quote_liquidity_when_oanda_omits_units_available(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    client.get_prices = lambda instruments: _quote_without_units_available(instruments)
    executor = _executor(client)

    result = await executor.run_cycle()

    assert result[0]["status"] == "filled"
    assert result[0]["units"] == 2500


@pytest.mark.asyncio
async def test_auto_execution_falls_back_to_quote_liquidity_when_side_units_are_omitted(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    client.get_prices = lambda instruments: _quote_without_long_units(instruments)
    executor = _executor(client)

    result = await executor.run_cycle()

    assert result[0]["status"] == "filled"
    assert result[0]["units"] == 2500


@pytest.mark.asyncio
async def test_auto_execution_uses_units_available_when_quote_liquidity_is_missing(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    client.get_prices = lambda instruments: _quote_without_visible_depth(instruments)
    executor = _executor(client)

    result = await executor.run_cycle()

    assert result[0]["status"] == "filled"
    assert result[0]["units"] == 1500


@pytest.mark.asyncio
async def test_automatic_order_prices_protection_from_side_book_vwap(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))

    async def get_book(instruments: tuple[str, ...]) -> list[OandaPrice]:
        del instruments
        return [
            OandaPrice(
                instrument="EUR_USD",
                time="2026-10-03T18:00:00Z",
                bid=Decimal("1.1000"),
                ask=Decimal("1.1002"),
                bids=((Decimal("1.1000"), Decimal(4000)),),
                asks=(
                    (Decimal("1.1002"), Decimal(1000)),
                    (Decimal("1.1012"), Decimal(1500)),
                ),
                units_available={"default": {"long": "2500", "short": "1800"}},
            )
        ]

    client.get_prices = get_book
    executor = _executor(client)
    executor.setup["pair_approved_revisions"]["EUR_USD"]["hyperopt"] = {
        "stopLoss": {"mode": "pips", "value": "10"},
    }

    result = await executor.run_cycle()

    assert result[0]["status"] == "filled"
    assert client.orders[0][1]["stop_loss_price"] == "1.0998"
    assert client.orders[0][1]["take_profit_price"] == "1.1028"


@pytest.mark.asyncio
async def test_short_order_protection_uses_bid_book_vwap(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("short"))

    async def get_book(instruments: tuple[str, ...]) -> list[OandaPrice]:
        del instruments
        return [
            OandaPrice(
                instrument="EUR_USD",
                time="2026-10-03T18:00:00Z",
                bid=Decimal("1.1000"),
                ask=Decimal("1.1002"),
                bids=(
                    (Decimal("1.1000"), Decimal(1000)),
                    (Decimal("1.0990"), Decimal(1500)),
                ),
                asks=((Decimal("1.1002"), Decimal(4000)),),
                units_available={"default": {"long": "1500", "short": "2500"}},
            )
        ]

    client.get_prices = get_book
    executor = _executor(client)

    result = await executor.run_cycle()

    assert result[0]["status"] == "filled"
    assert client.orders[0][1]["stop_loss_price"] == "1.1004"
    assert client.orders[0][1]["take_profit_price"] == "1.0974"


@pytest.mark.asyncio
async def test_trailing_stop_is_rounded_up_to_oanda_precision_and_minimum(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    get_instruments = client.get_instruments

    async def instrument_with_trailing_constraints(instruments: tuple[str, ...]):
        metadata = (await get_instruments(instruments))[0]
        return [
            OandaInstrument(
                name=metadata.name,
                display_name=metadata.display_name,
                pip_location=metadata.pip_location,
                display_precision=metadata.display_precision,
                trade_units_precision=metadata.trade_units_precision,
                minimum_trade_size=metadata.minimum_trade_size,
                base_currency=metadata.base_currency,
                quote_currency=metadata.quote_currency,
                margin_rate=metadata.margin_rate,
                minimum_trailing_stop_distance=Decimal("0.000605"),
                maximum_trailing_stop_distance=Decimal("0.01"),
            )
        ]

    client.get_instruments = instrument_with_trailing_constraints
    executor = _executor(client)
    executor.setup["pair_approved_revisions"]["EUR_USD"]["hyperopt"] = {
        "stopLoss": {"mode": "pips", "value": "6.02"},
        "trailingStopLoss": True,
    }
    executor.risk_configs["EUR/USD"]["stopLoss"] = None
    client.open_trades = [
        {
            "id": "auto-trailing",
            "instrument": "EUR_USD",
            "currentUnits": "1000",
            "price": "1.0990",
            "tradeClientExtensions": {"tag": "auto"},
        }
    ]

    result = await executor.run_cycle()

    assert result[0]["status"] == "trailing_activated"
    assert client.order_modifications == [
        (
            "auto-trailing",
            {"trailing_stop_loss_distance": "0.00061", "instrument": "EUR_USD"},
        )
    ]
    assert client.orders == []


@pytest.mark.asyncio
async def test_oanda_trailing_stop_requires_pips(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    executor = _executor(client)
    executor.setup["pair_approved_revisions"]["EUR_USD"]["hyperopt"] = {
        "stopLoss": {"mode": "percent", "value": "1"},
        "trailingStopLoss": True,
    }
    executor.risk_configs["EUR/USD"]["stopLoss"] = None

    result = await executor.run_cycle()

    assert result[0]["status"] == "blocked"
    assert result[0]["reason"] == "OANDA trailing stops must be configured in pips"
    assert client.orders == []


@pytest.mark.asyncio
async def test_automatic_order_rejection_reports_signal_and_candle(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))

    async def reject_order(*args, **kwargs):
        del args, kwargs
        raise OandaAPIError("OANDA rejected trailing stop precision")

    client.create_market_order = reject_order
    executor = _executor(client)

    result = await executor.run_cycle()

    assert result[0]["status"] == "blocked"
    assert result[0]["signal"] == "long"
    assert result[0]["candleTime"] == datetime(2026, 10, 3, 17, 55, tzinfo=UTC).isoformat()
    assert "precision" in str(result[0]["reason"])


@pytest.mark.asyncio
async def test_filled_result_reports_oanda_fill_units(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))

    async def filled_with_different_units(*args, **kwargs) -> OandaOrderResult:
        client.orders.append((args, kwargs))
        return OandaOrderResult(
            order_id="order-1",
            transaction_id="transaction-1",
            fill_price=Decimal("1.1001"),
            units=Decimal("303555"),
        )

    client.create_market_order = filled_with_different_units
    executor = _executor(client)

    result = await executor.run_cycle()

    assert result[0]["status"] == "filled"
    assert result[0]["units"] == 303555


@pytest.mark.asyncio
async def test_automatic_order_respects_panel_units_exposure_cap(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("short"))
    executor = _executor(client)
    executor.risk_configs["EUR/USD"]["maxExposure"] = "1200"
    executor.risk_configs["EUR/USD"]["maxExposureMode"] = "units"

    results = await executor.run_cycle()

    assert results[0]["status"] == "filled"
    assert client.orders[0][0] == ("EUR_USD", -1200)


@pytest.mark.asyncio
async def test_automatic_order_respects_available_margin_percent_cap(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("short"))
    executor = _executor(client)
    executor.risk_configs["EUR/USD"]["maxExposure"] = "0.1"
    executor.risk_configs["EUR/USD"]["maxExposureMode"] = "margin_percent"

    results = await executor.run_cycle()

    assert results[0]["status"] == "filled"
    assert client.orders[0][0] == ("EUR_USD", -409)


@pytest.mark.asyncio
async def test_automatic_order_uses_gbp_margin_cap_and_panel_stop_over_strategy_trailing(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    executor = _executor(client)
    executor.setup["pair_approved_revisions"]["EUR_USD"]["hyperopt"] = {
        "trailingStopLoss": True,
        "stopLoss": {"mode": "pips", "value": "40"},
    }
    executor.risk_configs["EUR/USD"]["maxExposure"] = "1"
    executor.risk_configs["EUR/USD"]["maxExposureMode"] = "margin_gbp"

    async def gbp_to_usd(
        source_currency: str | None,
        account_currency: str,
        *,
        preferred_instrument: str | None = None,
    ) -> OandaHomeConversion:
        del preferred_instrument
        if source_currency == "GBP" and account_currency == "USD":
            return OandaHomeConversion(
                currency="GBP",
                account_gain=Decimal("1.25"),
                account_loss=Decimal("1.25"),
                position_value=Decimal("1.25"),
            )
        return OandaHomeConversion(
            currency=account_currency,
            account_gain=Decimal(1),
            account_loss=Decimal(1),
            position_value=Decimal(1),
        )

    executor._quote_to_account_conversion = gbp_to_usd

    results = await executor.run_cycle()

    assert results[0]["status"] == "filled"
    assert client.orders[0][0] == ("EUR_USD", 56)
    assert client.orders[0][1]["stop_loss_price"] == "1.0992"
    assert client.orders[0][1]["trailing_stop_loss_distance"] is None


@pytest.mark.asyncio
async def test_none_side_blocks_new_automatic_entries(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    executor = _executor(client)
    executor.risk_configs["EUR/USD"]["side"] = "NONE"

    results = await executor.run_cycle()

    assert results[0]["status"] == "blocked"
    assert "does not allow long" in str(results[0]["reason"])
    assert client.orders == []


@pytest.mark.asyncio
async def test_opposite_signal_closes_automated_trade_before_reversing(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("short"))
    client.open_trades = [
        {
            "id": "trade-1",
            "instrument": "EUR_USD",
            "currentUnits": "1000",
            "tradeClientExtensions": {"tag": "auto"},
        }
    ]
    executor = _executor(client)
    executor.risk_configs["EUR/USD"]["takeProfit"] = "30"

    results = await executor.run_cycle()

    assert client.closed_trade_ids == ["trade-1"]
    assert len(client.orders) == 1
    assert client.orders[0][0] == ("EUR_USD", -1800)
    assert results[0]["status"] == "filled"


@pytest.mark.asyncio
async def test_opposite_signal_does_not_reverse_when_close_is_not_filled(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("short"))
    client.open_trades = [
        {
            "id": "trade-1",
            "instrument": "EUR_USD",
            "currentUnits": "1000",
            "tradeClientExtensions": {"tag": "auto"},
        }
    ]
    client.close_result = {"orderCancelTransaction": {"reason": "MARKET_HALTED"}}
    executor = _executor(client)

    results = await executor.run_cycle()

    assert results[0]["status"] == "close_not_filled"
    assert client.orders == []


@pytest.mark.asyncio
async def test_approved_optimized_stop_distance_overrides_risk_config(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    executor = _executor(client)
    executor.setup["pair_approved_revisions"]["EUR_USD"]["hyperopt"] = {
        "stopLoss": {"mode": "pips", "value": "5.0", "optimized": True},
    }
    executor.risk_configs["EUR/USD"]["stopLossSource"] = "approved-hyperopt"

    results = await executor.run_cycle()

    assert results[0]["status"] == "filled"
    assert Decimal(client.orders[0][1]["stop_loss_price"]) == Decimal("1.0997")


@pytest.mark.asyncio
async def test_trailing_stop_stays_unmodified_until_profit_covers_distance_and_buffer(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    client.open_trades = [
        {
            "id": "auto-trailing",
            "instrument": "EUR_USD",
            "currentUnits": "1000",
            "price": "1.0999",
            "tradeClientExtensions": {"tag": "auto"},
        }
    ]
    executor = _executor(client)
    executor.setup["pair_approved_revisions"]["EUR_USD"]["hyperopt"] = {
        "stopLoss": {"mode": "pips", "value": "5.0", "optimized": True},
        "trailingStopLoss": True,
    }

    results = await executor.run_cycle()

    assert results[0]["status"] == "blocked"
    assert client.order_modifications == []
    assert client.open_trades[0]["price"] == "1.0999"


@pytest.mark.asyncio
async def test_short_trailing_stop_activates_using_ask_after_profitable_move(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("short"))
    client.open_trades = [
        {
            "id": "auto-short-trailing",
            "instrument": "EUR_USD",
            "currentUnits": "-1000",
            "price": "1.1011",
            "tradeClientExtensions": {"tag": "auto"},
        }
    ]
    executor = _executor(client)
    executor.setup["pair_approved_revisions"]["EUR_USD"]["hyperopt"] = {
        "stopLoss": {"mode": "pips", "value": "5.0", "optimized": True},
        "trailingStopLoss": True,
    }

    results = await executor.run_cycle()

    assert results[0]["status"] == "trailing_activated"
    assert client.order_modifications == [
        (
            "auto-short-trailing",
            {"trailing_stop_loss_distance": "0.0005", "instrument": "EUR_USD"},
        )
    ]


@pytest.mark.asyncio
async def test_existing_trailing_stop_is_not_replaced_or_reset(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    client.open_trades = [
        {
            "id": "auto-trailing",
            "instrument": "EUR_USD",
            "currentUnits": "1000",
            "price": "1.0990",
            "trailingStopLossOrder": {"id": "existing-trailing"},
            "tradeClientExtensions": {"tag": "auto"},
        }
    ]
    executor = _executor(client)
    executor.setup["pair_approved_revisions"]["EUR_USD"]["hyperopt"] = {
        "stopLoss": {"mode": "pips", "value": "5.0", "optimized": True},
        "trailingStopLoss": True,
    }

    await executor.run_cycle()

    assert client.order_modifications == []


@pytest.mark.asyncio
@pytest.mark.parametrize("trailing_enabled", [True, False])
async def test_approved_trailing_stop_flag_controls_oanda_order_protection(
    tmp_path,
    monkeypatch,
    trailing_enabled,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    executor = _executor(client)
    executor.setup["pair_approved_revisions"]["EUR_USD"]["hyperopt"] = {
        "stopLoss": {"mode": "pips", "value": "5.0", "optimized": True},
        "trailingStopLoss": trailing_enabled,
    }
    executor.risk_configs["EUR/USD"]["stopLoss"] = None

    results = await executor.run_cycle()

    assert results[0]["status"] == "filled"
    assert Decimal(client.orders[0][1]["stop_loss_price"]) == Decimal("1.0997")
    assert client.orders[0][1]["trailing_stop_loss_distance"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("roi_enabled", [True, False])
async def test_approved_minimal_roi_closes_profitable_automated_trade(
    tmp_path,
    monkeypatch,
    roi_enabled,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    client.open_trades = [
        {
            "id": "trade-roi",
            "instrument": "EUR_USD",
            "currentUnits": "1000",
            "price": "1.0990",
            "openTime": "2026-10-03T17:45:00Z",
            "tradeClientExtensions": {"tag": "auto"},
        }
    ]
    executor = _executor(client)
    executor.risk_configs["EUR/USD"]["takeProfit"] = None
    executor.setup["pair_approved_revisions"]["EUR_USD"]["hyperopt"] = {
        "minimal_roi": {"0": 0.0005},
        "optimizeRoi": roi_enabled,
    }

    results = await executor.run_cycle()

    assert client.closed_trade_ids == (["trade-roi"] if roi_enabled else [])
    assert client.orders == []
    if roi_enabled:
        assert results[0]["status"] == "closed"


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_signal_enabled", [True, False])
async def test_approved_exit_signal_setting_overrides_strategy_default(
    tmp_path,
    monkeypatch,
    exit_signal_enabled,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    client.open_trades = [
        {
            "id": "trade-exit-signal",
            "instrument": "EUR_USD",
            "currentUnits": "1000",
            "price": "1.0990",
            "openTime": "2026-10-03T17:45:00Z",
            "tradeClientExtensions": {"tag": "auto"},
        }
    ]
    executor = _executor(client)
    executor.risk_configs["EUR/USD"]["takeProfit"] = None
    executor.setup["pair_approved_revisions"]["EUR_USD"]["hyperopt"] = {
        "useExitSignal": exit_signal_enabled,
        "optimizeRoi": False,
    }
    monkeypatch.setattr(
        "freqtrade.forex.auto_execution.FreqtradeStrategyAdapter.exit_signal",
        lambda self, candles, side: True,
    )

    await executor.run_cycle()

    assert client.closed_trade_ids == (
        ["trade-exit-signal"] if exit_signal_enabled else []
    )


@pytest.mark.asyncio
async def test_disabled_exit_signals_do_not_close_automated_trade_on_reversal(
    tmp_path,
    monkeypatch,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("short"))
    client.open_trades = [
        {
            "id": "trade-reversal",
            "instrument": "EUR_USD",
            "currentUnits": "1000",
            "price": "1.0990",
            "openTime": "2026-10-03T17:45:00Z",
            "tradeClientExtensions": {"tag": "auto"},
        }
    ]
    executor = _executor(client)
    executor.risk_configs["EUR/USD"]["takeProfit"] = None
    executor.setup["pair_approved_revisions"]["EUR_USD"]["hyperopt"] = {
        "useExitSignal": False,
        "optimizeRoi": False,
    }
    monkeypatch.setattr(
        "freqtrade.forex.auto_execution.FreqtradeStrategyAdapter.exit_signal",
        lambda self, candles, side: False,
    )

    await executor.run_cycle()

    assert client.closed_trade_ids == []
    assert client.orders == []


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_trigger", ["strategy", "roi"])
async def test_configured_take_profit_suppresses_strategy_and_roi_exits(
    tmp_path,
    monkeypatch,
    exit_trigger,
) -> None:
    _strategy_directory(tmp_path, monkeypatch)
    client = FakeOandaClient(_candles("long"))
    client.open_trades = [
        {
            "id": "trade-take-profit",
            "instrument": "EUR_USD",
            "currentUnits": "1000",
            "price": "1.0990",
            "openTime": "2026-10-03T17:45:00Z",
            "tradeClientExtensions": {"tag": "auto"},
        }
    ]
    executor = _executor(client)
    executor.risk_configs["EUR/USD"]["takeProfit"] = "30"
    if exit_trigger == "strategy":
        monkeypatch.setattr(
            "freqtrade.forex.auto_execution.FreqtradeStrategyAdapter.exit_signal",
            lambda self, candles, side: True,
        )
    else:
        executor.setup["pair_approved_revisions"]["EUR_USD"]["hyperopt"] = {
            "minimal_roi": {"0": 0.0005},
        }

    results = await executor.run_cycle()

    assert client.closed_trade_ids == []
    assert results[0]["status"] == "blocked"
    assert client.orders == []


async def _no_depth_quote(instruments: tuple[str, ...]) -> list[OandaPrice]:
    del instruments
    return [
        OandaPrice(
            instrument="EUR_USD",
            time="2026-10-03T18:00:00Z",
            bid=Decimal("1.1000"),
            ask=Decimal("1.1002"),
        )
    ]


async def _quote_without_units_available(
    instruments: tuple[str, ...],
) -> list[OandaPrice]:
    del instruments
    return [
        OandaPrice(
            instrument="EUR_USD",
            time="2026-10-03T18:00:00Z",
            bid=Decimal("1.1000"),
            ask=Decimal("1.1002"),
            bids=((Decimal("1.1000"), Decimal(4000)),),
            asks=((Decimal("1.1002"), Decimal(2500)),),
        )
    ]


async def _quote_without_visible_depth(
    instruments: tuple[str, ...],
) -> list[OandaPrice]:
    del instruments
    return [
        OandaPrice(
            instrument="EUR_USD",
            time="2026-10-03T18:00:00Z",
            bid=Decimal("1.1000"),
            ask=Decimal("1.1002"),
            units_available={"default": {"long": "1500", "short": "1800"}},
        )
    ]


async def _quote_without_long_units(
    instruments: tuple[str, ...],
) -> list[OandaPrice]:
    del instruments
    return [
        OandaPrice(
            instrument="EUR_USD",
            time="2026-10-03T18:00:00Z",
            bid=Decimal("1.1000"),
            ask=Decimal("1.1002"),
            bids=((Decimal("1.1000"), Decimal(4000)),),
            asks=((Decimal("1.1002"), Decimal(2500)),),
            units_available={"default": {"short": "1800"}},
        )
    ]


def test_live_execution_requires_the_live_account_mode(monkeypatch) -> None:
    with pytest.raises(AutoExecutionError, match="environment and execution mode"):
        OandaAutoStrategyExecutor(
            FakeOandaClient(_candles("long")),
            OandaSettings(
                "token",
                "test-account",
                environment=OandaEnvironment.LIVE,
                execution_mode="practice",
                live_trading_approved=True,
            ),
            {},
            {},
        )


def test_auto_execution_api_requires_approved_strategy_before_enable(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OANDA_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("OANDA_AUTO_EXECUTION_STATE_PATH", str(tmp_path / "auto-execution.json"))
    monkeypatch.setenv("OANDA_TOKEN", "test-token")
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("OANDA_ENVIRONMENT", "practice")
    monkeypatch.setenv("OANDA_EXECUTION_MODE", "practice")
    monkeypatch.setenv("OANDA_INSTRUMENTS", "EUR_USD")
    monkeypatch.setenv("OANDA_RISK_CONFIG_PATH", str(tmp_path / "risk-config.json"))
    (tmp_path / "config.json").write_text(
        '{"pair_strategies": {}, "pair_timeframes": {}, "pair_approved_revisions": {}}',
        encoding="utf-8",
    )

    with TestClient(create_app(tmp_path / "api.sqlite")) as client:
        status = client.get("/api/v1/strategy/auto-execution")
        default_risk = client.get("/api/v1/account/risk/config?pair=EUR%2FUSD")
        saved_risk = client.post(
            "/api/v1/account/risk/config",
            json={
                "pair": "EUR/USD",
                "side": "BOTH",
                "riskBudget": "0.5%",
                "maxExposure": "$10,000",
                "maxExposureMode": "absolute",
                "stopLoss": "10",
                "stopLossMode": "pips",
            },
            headers=_auth_headers(client),
        )
        saved_other_pair = client.post(
            "/api/v1/account/risk/config",
            json={
                "pair": "GBP/USD",
                "side": "SHORT",
                "riskBudget": "0.25%",
                "maxExposure": "$5,000",
                "maxExposureMode": "absolute",
                "stopLoss": "12",
                "stopLossMode": "pips",
            },
            headers=_auth_headers(client),
        )
        eur_after_gbp_save = client.get("/api/v1/account/risk/config?pair=EUR%2FUSD")
        gbp_after_save = client.get("/api/v1/account/risk/config?pair=GBP%2FUSD")
        enable = client.post(
            "/api/v1/strategy/auto-execution",
            json={"enabled": True},
            headers=_auth_headers(client),
        )
        state_path = tmp_path / "auto-execution.json"
        state_path.write_text(
            '{"enabled": true, "lastCycleAt": "2026-10-15T10:00:00Z", '
            '"lastError": null, "results": []}',
            encoding="utf-8",
        )
        active_status = client.get("/api/v1/strategy/auto-execution")
        state_path.write_text(
            '{"enabled": false, "lastProcessed": {}, "results": []}',
            encoding="utf-8",
        )

    with TestClient(create_app(tmp_path / "api-restarted.sqlite")) as restarted_client:
        restored_risk = restarted_client.get("/api/v1/account/risk/config?pair=EUR%2FUSD")
        restored_other_pair = restarted_client.get("/api/v1/account/risk/config?pair=GBP%2FUSD")

    assert status.status_code == 200
    assert status.json()["enabled"] is False
    assert default_risk.status_code == 200
    assert default_risk.json()["side"] == "NONE"
    assert "riskBudget" not in default_risk.json()
    assert "riskBudgetMode" not in default_risk.json()
    assert saved_risk.status_code == 200
    assert "riskBudget" not in saved_risk.json()
    assert "riskBudgetMode" not in saved_risk.json()
    assert "units" not in saved_risk.json()
    assert saved_other_pair.status_code == 200
    assert "units" not in saved_other_pair.json()
    assert "units" not in eur_after_gbp_save.json()
    assert eur_after_gbp_save.json()["side"] == "BOTH"
    assert "units" not in gbp_after_save.json()
    assert gbp_after_save.json()["side"] == "SHORT"
    assert restored_risk.status_code == 200
    assert restored_risk.json()["side"] == "BOTH"
    assert "units" not in restored_risk.json()
    assert restored_other_pair.json()["side"] == "SHORT"
    assert "units" not in restored_other_pair.json()
    assert enable.status_code == 409
    assert "approved strategy revision" in enable.json()["detail"]
    assert active_status.status_code == 200
    assert active_status.json()["enabled"] is True
    assert active_status.json()["environment"] == "practice"
    assert active_status.json()["lastCycleAt"] == "2026-10-15T10:00:00Z"
    assert active_status.json()["workerRunning"] is False
    assert "worker is not running" in active_status.json()["lastError"]


def test_broker_positions_show_the_live_trailing_stop_price(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OANDA_TOKEN", "test-token")
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("OANDA_ENVIRONMENT", "practice")
    monkeypatch.setenv("OANDA_EXECUTION_MODE", "practice")

    class PositionClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_account_summary(self):
            return OandaAccountState(
                account_id="test-account",
                currency="GBP",
                balance=Decimal(10000),
                nav=Decimal(10000),
                margin_available=Decimal(9000),
                unrealized_pl=Decimal(209),
            )

        async def get_open_trades(self):
            return [
                {
                    "id": "trailing-trade",
                    "instrument": "GBP_USD",
                    "currentUnits": "1000",
                    "price": "1.33170",
                    "trailingStopLossOrder": {
                        "trailingStopValue": "1.33195",
                        "distance": "0.00070",
                    },
                    "tradeClientExtensions": {"id": "auto-gbp-usd"},
                    "unrealizedPL": "209.2690",
                }
            ]

        async def get_closed_trades(self, *, count):
            return []

        async def get_prices(self, instruments):
            assert instruments == ["GBP_USD"]
            return [
                OandaPrice(
                    instrument="GBP_USD",
                    time="2026-10-19T09:30:00Z",
                    bid=Decimal("1.33265"),
                    ask=Decimal("1.33267"),
                )
            ]

    monkeypatch.setattr("freqtrade.forex.api.OandaClient", PositionClient)

    with TestClient(create_app(tmp_path / "api.sqlite")) as client:
        response = client.get("/api/v1/positions")

    assert response.status_code == 200, response.text
    assert response.json()["open"][0]["stopLoss"] == "1.33195"


def test_auto_execution_reports_missing_stop_loss_for_all_configured_pairs(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OANDA_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("OANDA_AUTO_EXECUTION_STATE_PATH", str(tmp_path / "auto-execution.json"))
    monkeypatch.setenv("OANDA_TOKEN", "test-token")
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("OANDA_ENVIRONMENT", "practice")
    monkeypatch.setenv("OANDA_EXECUTION_MODE", "practice")
    monkeypatch.setenv("OANDA_INSTRUMENTS", "EUR_USD,GBP_USD")
    monkeypatch.setenv("OANDA_RISK_CONFIG_PATH", str(tmp_path / "risk-config.json"))
    setup = {
        "pair_strategies": {"EUR_USD": "TestStrategy", "GBP_USD": "TestStrategy"},
        "pair_timeframes": {"EUR_USD": "5m", "GBP_USD": "5m"},
        "pair_approved_revisions": {
            "EUR_USD": {
                "pair": "EUR/USD",
                "strategyClass": "TestStrategy",
                "timeframe": "5m",
            },
            "GBP_USD": {
                "pair": "GBP/USD",
                "strategyClass": "TestStrategy",
                "timeframe": "5m",
            },
        },
    }
    (tmp_path / "config.json").write_text(json.dumps(setup), encoding="utf-8")

    with TestClient(create_app(tmp_path / "api.sqlite")) as client:
        response = client.post(
            "/api/v1/strategy/auto-execution",
            json={"enabled": True},
            headers=_auth_headers(client),
        )
        status_after_rejection = client.get("/api/v1/strategy/auto-execution")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "Automatic execution was not started" in detail
    assert "Configure a stop loss for EUR/USD in Risk controls" in detail
    assert "Configure a stop loss for GBP/USD in Risk controls" in detail
    assert status_after_rejection.json()["enabled"] is False
    assert status_after_rejection.json()["lastError"] == detail


def test_approved_hyperopt_stop_loss_is_risk_default_until_operator_override(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OANDA_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("OANDA_AUTO_EXECUTION_STATE_PATH", str(tmp_path / "auto-execution.json"))
    monkeypatch.setenv("OANDA_TOKEN", "test-token")
    monkeypatch.setenv("OANDA_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("OANDA_ENVIRONMENT", "practice")
    monkeypatch.setenv("OANDA_EXECUTION_MODE", "practice")
    monkeypatch.setenv("OANDA_INSTRUMENTS", "EUR_USD")
    monkeypatch.setenv("OANDA_RISK_CONFIG_PATH", str(tmp_path / "risk-config.json"))
    setup = {
        "pair_strategies": {"EUR_USD": "TestStrategy"},
        "pair_timeframes": {"EUR_USD": "5m"},
        "pair_approved_revisions": {
            "EUR_USD": {
                "pair": "EUR/USD",
                "strategyClass": "TestStrategy",
                "timeframe": "5m",
                "hyperopt": {
                    "stopLoss": {
                        "mode": "pips",
                        "value": "15",
                        "optimized": False,
                    },
                    "trailingStopLoss": False,
                },
            }
        },
    }
    (tmp_path / "config.json").write_text(json.dumps(setup), encoding="utf-8")

    class NoOandaRequests:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get_account_summary(self):
            return None

        async def get_open_trades(self):
            return []

        async def get_candles(self, instrument, granularity, *, count):
            return []

    monkeypatch.setattr("freqtrade.forex.api.OandaClient", NoOandaRequests)
    with TestClient(create_app(tmp_path / "api.sqlite")) as client:
        headers = _auth_headers(client)
        inherited = client.get("/api/v1/account/risk/config?pair=EUR%2FUSD")
        enable = client.post(
            "/api/v1/strategy/auto-execution",
            json={"enabled": True},
            headers=headers,
        )
        disable = client.post(
            "/api/v1/strategy/auto-execution",
            json={"enabled": False},
            headers=headers,
        )
        unrelated_update = client.post(
            "/api/v1/account/risk/config",
            json={
                **inherited.json(),
                "maxExposure": "25",
            },
            headers=headers,
        )
        after_unrelated_update = client.get(
            "/api/v1/account/risk/config?pair=EUR%2FUSD"
        )
        explicit_override = client.post(
            "/api/v1/account/risk/config",
            json={
                **after_unrelated_update.json(),
                "stopLoss": "20",
            },
            headers=headers,
        )
        after_override = client.get("/api/v1/account/risk/config?pair=EUR%2FUSD")

    assert inherited.status_code == 200
    assert inherited.json()["stopLoss"] == "15"
    assert inherited.json()["stopLossMode"] == "pips"
    assert inherited.json()["stopLossSource"] == "approved-hyperopt"
    assert inherited.json()["stopLossOverride"] is False
    assert enable.status_code == 200, enable.text
    assert enable.json()["enabled"] is True
    assert disable.status_code == 200, disable.text
    assert unrelated_update.status_code == 200, unrelated_update.text
    assert unrelated_update.json()["stopLossOverride"] is False
    assert after_unrelated_update.json()["stopLoss"] == "15"
    assert after_unrelated_update.json()["stopLossSource"] == "approved-hyperopt"
    assert explicit_override.status_code == 200, explicit_override.text
    assert explicit_override.json()["stopLossOverride"] is True
    assert after_override.json()["stopLoss"] == "20"
    assert after_override.json()["stopLossSource"] == "operator-config"
