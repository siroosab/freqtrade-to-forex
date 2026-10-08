import json
from decimal import Decimal

import httpx
import pandas as pd
import pytest

from freqtrade.forex.backtest import ForexBacktester
from freqtrade.forex.models import OandaEnvironment, OandaInstrument
from freqtrade.forex.oanda import OandaClient
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


@pytest.mark.asyncio
async def test_oanda_client_creates_market_order_with_native_trailing_stop_on_fill() -> None:
    captured: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
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
            await client.create_market_order(
                "EUR_USD",
                1000,
                trailing_stop_loss_distance="0.0005",
                client_order_id="trailing-entry-1",
            )

    assert captured is not None
    order = json.loads(captured.content)["order"]
    assert order["trailingStopLossOnFill"] == {
        "timeInForce": "GTC",
        "distance": "0.0005",
    }
    assert "stopLossOnFill" not in order


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("distance", "message"),
    [
        ("0.0001", "below OANDA's instrument minimum"),
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
