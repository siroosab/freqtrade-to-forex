import json
from decimal import Decimal

import httpx
import pandas as pd
import pytest

from freqtrade.forex.backtest import ForexBacktester
from freqtrade.forex.models import OandaEnvironment, OandaInstrument, OandaOrderResult
from freqtrade.forex.oanda import OandaClient
from freqtrade.forex.strategy_hyperopt import _sample_stop_distance_pips
from freqtrade.forex.strategy_loop import EmaCrossStrategy, Signal


def test_backtest_trailing_stop_ratchets_and_closes_at_the_following_candle() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal(1),
    )
    dates = pd.date_range("2026-01-01", periods=2, freq="5min", tz="UTC")
    candles = pd.DataFrame(
        {
            "date": dates,
            "open": [1.101, 1.102],
            "high": [1.103, 1.103],
            "low": [1.101, 1.1018],
            "close": [1.102, 1.102],
            "volume": [0, 0],
        }
    )
    common = {
        "starting_balance": Decimal(10000),
        "risk_fraction": Decimal("0.01"),
        "stop_pips": Decimal(10),
        "spread": Decimal(0),
    }
    position = (Signal.LONG, 1000, Decimal("1.1000"), dates[0], dates[0])

    trailing = ForexBacktester(
        EmaCrossStrategy(),
        instrument,
        **common,
        trailing_stop_loss=True,
    )._intrabar_trigger(
        position,
        candles,
        pd.DatetimeIndex(dates),
        start=dates[0],
        end=dates[-1] + pd.Timedelta(minutes=5),
        instrument=instrument,
    )
    fixed = ForexBacktester(EmaCrossStrategy(), instrument, **common)._intrabar_trigger(
        position,
        candles,
        pd.DatetimeIndex(dates),
        start=dates[0],
        end=dates[-1] + pd.Timedelta(minutes=5),
        instrument=instrument,
    )

    assert trailing == (dates[1], Decimal("1.10200"))
    assert fixed is None


def test_trailing_stop_does_not_activate_before_profitable_threshold() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal(1),
        minimum_trailing_stop_distance=Decimal("0.0005"),
    )
    dates = pd.date_range("2026-01-01", periods=2, freq="5min", tz="UTC")
    candles = pd.DataFrame(
        {
            "date": dates,
            "open": [1.1002, 1.1003],
            "high": [1.1005, 1.1005],
            "low": [1.1001, 1.1002],
            "close": [1.1003, 1.1003],
            "volume": [0, 0],
        }
    )
    result = ForexBacktester(
        EmaCrossStrategy(),
        instrument,
        starting_balance=Decimal(10000),
        risk_fraction=Decimal("0.01"),
        stop_pips=Decimal(1),
        spread=Decimal(0),
        trailing_stop_loss=True,
    )._intrabar_trigger(
        (Signal.LONG, 1000, Decimal("1.1000"), dates[0], dates[0]),
        candles,
        pd.DatetimeIndex(dates),
        start=dates[0],
        end=dates[-1] + pd.Timedelta(minutes=5),
        instrument=instrument,
    )

    assert result is None


def test_trailing_distance_and_units_use_instrument_precision() -> None:
    instrument = OandaInstrument(
        name="USD_JPY",
        display_name="USD/JPY",
        pip_location=-2,
        display_precision=3,
        trade_units_precision=0,
        minimum_trade_size=Decimal(1),
    )

    assert instrument.format_trailing_stop_distance(Decimal("0.05")) == "0.05"
    assert instrument.format_trailing_stop_distance(Decimal("0.0502")) == "0.051"
    assert instrument.format_units(303552) == "303552"
    with pytest.raises(ValueError, match="exceed 0 decimal places"):
        instrument.format_units(Decimal("303552.5"))


def test_automatic_hyperopt_stop_distance_clears_spread_and_slippage() -> None:
    instrument = OandaInstrument(
        name="EUR_USD",
        display_name="EUR/USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal(1),
    )
    dates = pd.date_range("2026-01-01", periods=3, freq="5min", tz="UTC")
    candles = pd.DataFrame(
        {
            "date": dates,
            "high": [1.1001, 1.1001, 1.1001],
            "low": [1.1000, 1.1000, 1.1000],
            "close": [1.10005, 1.10005, 1.10005],
        }
    )

    class LowestBoundRandom:
        @staticmethod
        def uniform(low: float, high: float) -> float:
            assert high >= low
            return low

    distance = _sample_stop_distance_pips(
        candles,
        instrument,
        LowestBoundRandom(),
        spread=Decimal("0.0002"),
        slippage=Decimal("0.0001"),
    )

    assert distance == Decimal("5.00")

    result = OandaOrderResult.from_payload(
        {
            "orderCreateTransaction": {"id": "order-1", "units": "303552"},
            "orderFillTransaction": {
                "id": "fill-1",
                "units": "303555",
                "price": "1.25000",
            },
            "lastTransactionID": "fill-1",
        }
    )
    assert result.units == Decimal("303555")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("distance", "minimum", "expected_distance"),
    [
        ("0.0005", "0.0002", "0.0005"),
        ("0.000602", "0.0002", "0.00061"),
        ("0.0005", "0.000605", "0.00061"),
    ],
)
async def test_oanda_client_creates_market_order_with_native_trailing_stop_on_fill(
    distance: str,
    minimum: str,
    expected_distance: str,
) -> None:
    captured: httpx.Request | None = None
    instrument_requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured, instrument_requests
        if request.url.path.endswith("/instruments"):
            instrument_requests += 1
            return httpx.Response(
                200,
                json={
                    "instruments": [
                        {
                            "name": "EUR_USD",
                            "displayName": "EUR/USD",
                            "pipLocation": -4,
                            "displayPrecision": 5,
                            "tradeUnitsPrecision": 0,
                            "minimumTradeSize": "1",
                            "minimumTrailingStopDistance": minimum,
                            "maximumTrailingStopDistance": "1.0",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/pricing"):
            return httpx.Response(
                200,
                json={
                    "prices": [
                        {
                            "instrument": "EUR_USD",
                            "time": "2026-09-15T10:00:00Z",
                            "bids": [{"price": "1.10000"}],
                            "asks": [{"price": "1.10012"}],
                        }
                    ],
                },
            )
        captured = request
        return httpx.Response(
            201,
            json={
                "lastTransactionID": "11",
                "orderCreateTransaction": {"id": "10", "units": "1000"},
                "orderFillTransaction": {"price": "1.10012"},
            },
        )

    async with httpx.AsyncClient(
        base_url=OandaEnvironment.PRACTICE.rest_url,
        transport=httpx.MockTransport(handler),
    ) as http_client:
        async with OandaClient("ignored", "account", http_client=http_client) as client:
            await client.get_instruments(("EUR_USD",))
            await client.create_market_order(
                "EUR_USD",
                1000,
                trailing_stop_loss_distance=distance,
                client_order_id="trailing-entry-1",
            )

    assert captured is not None
    order = json.loads(captured.content)["order"]
    assert order["trailingStopLossOnFill"] == {
        "timeInForce": "GTC",
        "distance": expected_distance,
    }
    assert order["units"] == "1000"
    assert "stopLossOnFill" not in order
    assert instrument_requests == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("distance", "message"),
    [
        ("1.1", "exceeds OANDA's instrument maximum"),
    ],
)
async def test_oanda_client_rejects_trailing_distances_outside_instrument_limits(
    distance: str,
    message: str,
) -> None:
    order_submitted = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal order_submitted
        if request.url.path.endswith("/instruments"):
            return httpx.Response(
                200,
                json={
                    "instruments": [
                        {
                            "name": "EUR_USD",
                            "displayName": "EUR/USD",
                            "pipLocation": -4,
                            "displayPrecision": 5,
                            "tradeUnitsPrecision": 0,
                            "minimumTradeSize": "1",
                            "minimumTrailingStopDistance": "0.0002",
                            "maximumTrailingStopDistance": "1.0",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/pricing"):
            return httpx.Response(
                200,
                json={
                    "prices": [
                        {
                            "instrument": "EUR_USD",
                            "time": "2026-09-15T10:00:00Z",
                            "bids": [{"price": "1.10000"}],
                            "asks": [{"price": "1.10012"}],
                        }
                    ],
                },
            )
        order_submitted = True
        return httpx.Response(500)

    async with httpx.AsyncClient(
        base_url=OandaEnvironment.PRACTICE.rest_url,
        transport=httpx.MockTransport(handler),
    ) as http_client:
        async with OandaClient("ignored", "account", http_client=http_client) as client:
            with pytest.raises(ValueError, match=message):
                await client.create_market_order(
                    "EUR_USD",
                    1000,
                    trailing_stop_loss_distance=distance,
                    client_order_id="invalid-trailing-entry",
                )

    assert order_submitted is False
