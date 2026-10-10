from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
    OandaInstrument,
    OandaOrderResult,
    OandaPrice,
)
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
    assert "depth" in str(result[0]["reason"]).lower()
    assert client.orders == []


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

    async def gbp_to_usd(source_currency: str | None, account_currency: str) -> Decimal:
        if source_currency == "GBP" and account_currency == "USD":
            return Decimal("1.25")
        return Decimal(1)

    executor._quote_to_account_rate = gbp_to_usd

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
    executor.risk_configs["EUR/USD"]["stopLoss"] = None

    results = await executor.run_cycle()

    assert results[0]["status"] == "filled"
    assert Decimal(client.orders[0][1]["stop_loss_price"]) == Decimal("1.0997")


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
    if trailing_enabled:
        assert client.orders[0][1]["stop_loss_price"] is None
        assert client.orders[0][1]["trailing_stop_loss_distance"] == "0.00050"
    else:
        assert Decimal(client.orders[0][1]["stop_loss_price"]) == Decimal("1.0997")
        assert client.orders[0][1]["trailing_stop_loss_distance"] is None


@pytest.mark.asyncio
async def test_approved_minimal_roi_closes_profitable_automated_trade(
    tmp_path,
    monkeypatch,
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
    }

    results = await executor.run_cycle()

    assert client.closed_trade_ids == ["trade-roi"]
    assert client.orders == []
    assert results[0]["status"] == "closed"


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


def test_live_execution_requires_the_live_account_mode(monkeypatch) -> None:
    monkeypatch.setenv("OANDA_LIVE_CONFIRM", "1")
    with pytest.raises(AutoExecutionError, match="environment and execution mode"):
        OandaAutoStrategyExecutor(
            FakeOandaClient(_candles("long")),
            OandaSettings(
                "token",
                "test-account",
                environment=OandaEnvironment.LIVE,
                execution_mode="practice",
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
