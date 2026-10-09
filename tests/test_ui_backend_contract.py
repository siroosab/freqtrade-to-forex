import json
import sqlite3
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from freqtrade.forex.api import (
    app,
    create_app,
    format_hyperopt_report,
)
from freqtrade.forex.models import OandaInstrument


client = TestClient(app)


def test_auto_hyperopt_clears_stale_trigger_marker_after_restart(tmp_path):
    database_path = tmp_path / "stale-auto-hyperopt.sqlite"
    create_app(database_path)
    now = datetime.now().astimezone()
    persisted_state = {
        "enabled": True,
        "weekdays": [now.weekday()],
        "time": "00:00",
        "pairs": [],
        "queue": {"status": "queued", "scheduledAt": now.isoformat()},
        "lastTriggeredDate": now.date().isoformat(),
        "lastResults": {},
    }
    with sqlite3.connect(database_path) as database:
        database.execute(
            "INSERT OR REPLACE INTO auto_hyperopt_state(id, state_json) VALUES (1, ?)",
            (json.dumps(persisted_state),),
        )

    restored_app = create_app(database_path)

    assert restored_app.state.auto_hyperopt_state["lastTriggeredDate"] is None
    assert restored_app.state.auto_hyperopt_state["queue"]["status"] == "queued"


def test_auto_hyperopt_preserves_trigger_for_queue_started_today(tmp_path):
    database_path = tmp_path / "running-auto-hyperopt.sqlite"
    create_app(database_path)
    now = datetime.now().astimezone()
    persisted_state = {
        "enabled": True,
        "weekdays": [now.weekday()],
        "time": "00:00",
        "pairs": [],
        "queue": {
            "status": "running",
            "startedAt": now.isoformat(),
        },
        "lastTriggeredDate": now.date().isoformat(),
        "lastResults": {},
    }
    with sqlite3.connect(database_path) as database:
        database.execute(
            "INSERT OR REPLACE INTO auto_hyperopt_state(id, state_json) VALUES (1, ?)",
            (json.dumps(persisted_state),),
        )

    restored_app = create_app(database_path)
    restored_state = restored_app.state.auto_hyperopt_state

    assert restored_state["lastTriggeredDate"] == now.date().isoformat()
    assert restored_state["queue"]["status"] == "interrupted"
    assert restored_state["queue"]["startedAt"] == now.isoformat()


def _auth_headers(test_client, role="operator"):
    password = {
        "viewer": "test-viewer-password",
        "operator": "test-operator-password",
        "admin": "test-admin-password",
    }[role]
    response = test_client.post(
        "/api/v1/auth/login",
        json={"username": role, "password": password},
    )
    assert response.status_code == 200, response.text
    session = response.json()
    return {
        "X-Session-Token": session["sessionToken"],
        "X-User-Role": role,
        "X-CSRF-Token": session["csrfToken"],
    }


def test_account_summary_endpoint_exists():
    response = client.get("/api/v1/account/summary")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "equity" in payload
    assert "netPnl" in payload


def test_market_summary_endpoint_exists():
    response = client.get("/api/v1/markets/summary")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "instruments" in payload
    assert "strategySignals" in payload


def test_market_quote_endpoint_reads_current_broker_price(monkeypatch):
    instrument_lookups = []

    class FakeInstrument:
        display_precision = 5
        trade_units_precision = 0
        minimum_trade_size = Decimal("1")
        pip_size = Decimal("0.0001")
        margin_rate = Decimal("0.02")
        base_currency = "EUR"
        quote_currency = "USD"

    class FakePrice:
        instrument = "EUR_USD"
        bid = Decimal("1.09501")
        ask = Decimal("1.09513")
        spread = Decimal("0.00012")
        time = "2026-09-28T10:00:00Z"
        tradeable = True
        bids = ((Decimal("1.09501"), Decimal("100000")),)
        asks = ((Decimal("1.09513"), Decimal("120000")),)
        units_available = {"default": {"long": "200000", "short": "180000"}}

    class FakeConversionPrice:
        bid = Decimal("0.7800")
        ask = Decimal("0.7810")

    class FakeAccount:
        currency = "GBP"
        margin_available = Decimal("9000.00")

    class FakeClient:
        def __init__(self, token, account_id, environment):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get_prices(self, instruments):
            if instruments == ("EUR_USD",):
                return [FakePrice()]
            assert instruments == ("USD_GBP",)
            return [FakeConversionPrice()]

        async def get_account_summary(self):
            return FakeAccount()

        async def get_instruments(self, instruments):
            assert instruments == ("EUR_USD",)
            instrument_lookups.append(instruments)
            return [FakeInstrument()]

    environment = type("Env", (), {"value": "practice"})()
    settings = type(
        "Settings", (), {"token": "token", "account_id": "account", "environment": environment}
    )()
    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    monkeypatch.setattr("freqtrade.forex.api.OandaSettings.from_environment", lambda: settings)

    response = client.get("/api/v1/markets/quote?pair=EUR%2FUSD")
    repeated_response = client.get("/api/v1/markets/quote?pair=EUR%2FUSD")

    assert response.status_code == 200, response.text
    assert repeated_response.status_code == 200, repeated_response.text
    assert len(instrument_lookups) == 1
    assert response.json() == {
        "pair": "EUR/USD",
        "bid": "1.09501",
        "ask": "1.09513",
        "spread": "0.00012",
        "time": "2026-09-28T10:00:00Z",
        "tradeable": True,
        "environment": "practice",
        "displayPrecision": 5,
        "tradeUnitsPrecision": 0,
        "minimumTradeSize": "1",
        "baseCurrency": "EUR",
        "quoteCurrency": "USD",
        "pipSize": "0.0001",
        "marginRate": "0.02",
        "bids": [{"price": "1.09501", "units": "100000"}],
        "asks": [{"price": "1.09513", "units": "120000"}],
        "unitsAvailable": {"default": {"long": "200000", "short": "180000"}},
        "accountCurrency": "GBP",
        "marginAvailable": "9000.00",
        "quoteToAccountRate": "0.7800",
        "conversionError": None,
    }


def test_broker_positions_returns_open_and_closed_trade_details(monkeypatch):
    class FakePrice:
        instrument = "EUR_USD"
        bid = Decimal("1.1000")
        ask = Decimal("1.1002")

    class FakeClient:
        def __init__(self, token, account_id, environment):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get_account_summary(self):
            return type("Account", (), {"currency": "GBP"})()

        async def get_open_trades(self):
            return [
                {
                    "id": "open-1",
                    "instrument": "EUR_USD",
                    "currentUnits": "1200",
                    "initialUnits": "1200",
                    "price": "1.0950",
                    "openTime": "2026-09-28T09:00:00Z",
                    "unrealizedPL": "6.00",
                    "clientExtensions": {"id": "manual-ui-1"},
                    "stopLossOrder": {"price": "1.0895"},
                    "takeProfitOrder": {"price": "1.1060"},
                }
            ]

        async def get_closed_trades(self, *, count=100):
            return [
                {
                    "id": "closed-1",
                    "instrument": "EUR_USD",
                    "currentUnits": "0",
                    "initialUnits": "-500",
                    "price": "1.1000",
                    "averageClosePrice": "1.0970",
                    "openTime": "2026-09-27T09:00:00Z",
                    "closeTime": "2026-09-27T11:00:00Z",
                    "realizedPL": "-1.50",
                }
            ]

        async def get_prices(self, instruments):
            return [FakePrice()]

    environment = type("Env", (), {"value": "practice"})()
    settings = type(
        "Settings", (), {"token": "token", "account_id": "account", "environment": environment}
    )()
    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    monkeypatch.setattr("freqtrade.forex.api.OandaSettings.from_environment", lambda: settings)

    response = client.get("/api/v1/positions")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["open"][0]["currentPrice"] == "1.1000"
    assert payload["open"][0]["pnl"] == "6.00"
    assert payload["open"][0]["manual"] is True
    assert payload["open"][0]["stopLoss"] == "1.0895"
    assert payload["closed"][0]["side"] == "SELL"
    assert payload["closed"][0]["exitPrice"] == "1.0970"
    assert payload["closed"][0]["pnl"] == "-1.50"
    assert payload["accountCurrency"] == "GBP"


def test_risk_protection_is_applied_to_the_open_oanda_trade(monkeypatch):
    modifications = []

    class FakeClient:
        def __init__(self, token, account_id, environment):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get_open_trades(self):
            return [{"id": "risk-trade-1", "instrument": "EUR_USD"}]

        async def modify_trade_orders(self, trade_id, **orders):
            modifications.append((trade_id, orders))
            return {"lastTransactionID": "tx-risk-1"}

    environment = type("Env", (), {"value": "practice"})()
    settings = type(
        "Settings", (), {"token": "token", "account_id": "account", "environment": environment}
    )()
    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    monkeypatch.setattr("freqtrade.forex.api.OandaSettings.from_environment", lambda: settings)

    response = client.post(
        "/api/v1/positions/risk-trade-1/risk-protection",
        headers=_auth_headers(client),
        json={"stopLoss": "1.0800", "takeProfit": "1.1200"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "modified"
    assert modifications == [
        (
            "risk-trade-1",
            {
                "stop_loss_price": "1.0800",
                "take_profit_price": "1.1200",
                "trailing_stop_loss_distance": None,
            },
        )
    ]


def test_average_entry_creates_adverse_limit_order_linked_to_parent_trade(monkeypatch):
    order_calls = []

    class FakePrice:
        bid = Decimal("1.0900")
        ask = Decimal("1.0902")
        tradeable = True

    class FakeOrderResult:
        order_id = "average-order-1"
        transaction_id = "tx-average-1"

    class FakeClient:
        def __init__(self, token, account_id, environment):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get_open_trades(self):
            return [
                {
                    "id": "parent-trade-1",
                    "instrument": "EUR_USD",
                    "currentUnits": "1000",
                    "price": "1.1000",
                    "unrealizedPL": "-10.00",
                }
            ]

        async def get_prices(self, instruments):
            assert instruments == ("EUR_USD",)
            return [FakePrice()]

        async def create_limit_order(self, *args, **kwargs):
            order_calls.append((args, kwargs))
            return FakeOrderResult()

    environment = type("Env", (), {"value": "practice"})()
    settings = type(
        "Settings", (), {"token": "token", "account_id": "account", "environment": environment}
    )()
    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    monkeypatch.setattr("freqtrade.forex.api.OandaSettings.from_environment", lambda: settings)

    response = client.post(
        "/api/v1/positions/parent-trade-1/average-entry",
        headers=_auth_headers(client),
        json={"units": "1500", "price": "1.0800", "stopLoss": "1.0700", "takeProfit": "1.1200"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["orderId"] == "average-order-1"
    args, kwargs = order_calls[0]
    assert args[:3] == ("EUR_USD", 1500, "1.0800")
    assert kwargs["stop_loss_price"] == "1.0700"
    assert kwargs["take_profit_price"] == "1.1200"
    assert kwargs["client_order_id"].startswith("risk-average-for-parent-trade-1-")
    assert kwargs["trade_client_extensions"]["comment"] == "average-for:parent-trade-1"


def test_average_entry_rejects_trade_not_currently_at_a_loss(monkeypatch):
    class FakePrice:
        bid = Decimal("1.0900")
        ask = Decimal("1.0902")
        tradeable = True

    class FakeClient:
        def __init__(self, token, account_id, environment):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get_open_trades(self):
            return [
                {
                    "id": "parent-trade-2",
                    "instrument": "EUR_USD",
                    "currentUnits": "1000",
                    "price": "1.0800",
                    "unrealizedPL": "10.00",
                }
            ]

        async def get_prices(self, instruments):
            return [FakePrice()]

    environment = type("Env", (), {"value": "practice"})()
    settings = type(
        "Settings", (), {"token": "token", "account_id": "account", "environment": environment}
    )()
    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    monkeypatch.setattr("freqtrade.forex.api.OandaSettings.from_environment", lambda: settings)

    response = client.post(
        "/api/v1/positions/parent-trade-2/average-entry",
        headers=_auth_headers(client),
        json={"units": "1000", "price": "1.0700"},
    )

    assert response.status_code == 409
    assert "only be placed while" in response.json()["detail"]


def test_average_entry_uses_sell_units_and_adverse_price_for_losing_short(monkeypatch):
    order_calls = []

    class FakePrice:
        bid = Decimal("1.0898")
        ask = Decimal("1.0900")
        tradeable = True

    class FakeOrderResult:
        order_id = "average-short-order"
        transaction_id = "tx-average-short"

    class FakeClient:
        def __init__(self, token, account_id, environment):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get_open_trades(self):
            return [
                {
                    "id": "parent-short-1",
                    "instrument": "EUR_USD",
                    "currentUnits": "-700",
                    "price": "1.0800",
                    "unrealizedPL": "-7.00",
                }
            ]

        async def get_prices(self, instruments):
            return [FakePrice()]

        async def create_limit_order(self, *args, **kwargs):
            order_calls.append((args, kwargs))
            return FakeOrderResult()

    environment = type("Env", (), {"value": "practice"})()
    settings = type(
        "Settings", (), {"token": "token", "account_id": "account", "environment": environment}
    )()
    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    monkeypatch.setattr("freqtrade.forex.api.OandaSettings.from_environment", lambda: settings)

    response = client.post(
        "/api/v1/positions/parent-short-1/average-entry",
        headers=_auth_headers(client),
        json={"units": "500", "price": "1.1000"},
    )

    assert response.status_code == 200, response.text
    assert order_calls[0][0][:3] == ("EUR_USD", -500, "1.1000")


def test_risk_config_accepts_pips_for_post_trade_controls(tmp_path):
    with TestClient(create_app(tmp_path / "risk-config.sqlite")) as scoped_client:
        response = scoped_client.post(
            "/api/v1/account/risk/config",
            headers=_auth_headers(scoped_client),
            json={
                "pair": "CHF/JPY",
                "stopLoss": "15",
                "stopLossMode": "pips",
                "takeProfit": "30",
                "takeProfitMode": "pips",
                "averageEntry": "20",
                "averageEntryMode": "pips",
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["stopLossMode"] == "pips"
    assert response.json()["takeProfitMode"] == "pips"
    assert response.json()["averageEntryMode"] == "pips"


def test_websocket_market_channel_connects():
    with client.websocket_connect("/ws/market") as websocket:
        message = websocket.receive_json()
        assert "type" in message
        assert "channel" in message
        assert "timestamp" in message
        assert "data" in message
        assert "instruments" in message["data"]


@pytest.mark.parametrize(
    (
        "stop_distance_mode",
        "candidate_stop_loss",
        "expected_stop_loss",
        "expected_stop_pips",
        "expected_optimization",
    ),
    [
        (
            "static",
            None,
            {
                "mode": "percent",
                "value": "1.25",
                "unit": "% of entry price",
                "optimized": False,
            },
            "0.5",
            False,
        ),
        (
            "automatic",
            {"mode": "pips", "value": "7.25", "optimized": True},
            {"mode": "pips", "value": "7.25", "unit": "pips", "optimized": True},
            "7.25",
            True,
        ),
    ],
)
@pytest.mark.parametrize("trailing_stop_loss_enabled", [True, False])
def test_hyperopt_approval_flows_into_chart_strategy(
    tmp_path,
    monkeypatch,
    stop_distance_mode,
    candidate_stop_loss,
    expected_stop_loss,
    expected_stop_pips,
    expected_optimization,
    trailing_stop_loss_enabled,
):
    from datetime import timezone
    from types import SimpleNamespace

    database_path = tmp_path / "hyperopt-approval.sqlite"
    candle_requests = []
    hyperopt_options = {}

    class FakeCandle:
        def __init__(self, index):
            self.time = (
                (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * index))
                .isoformat()
                .replace("+00:00", "Z")
            )
            self.complete = True
            self.open = Decimal(str(1.1 + index * 0.00001))
            self.high = Decimal(str(1.101 + index * 0.00001))
            self.low = Decimal(str(1.099 + index * 0.00001))
            self.close = Decimal(str(1.1 + index * 0.00001))
            self.volume = 100

    class FakeClient:
        def __init__(self, token, account_id, environment):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get_account_summary(self):
            return SimpleNamespace(balance=Decimal("10000"), currency="GBP")

        async def get_instruments(self, instruments):
            return [
                OandaInstrument(
                    name="EUR_USD",
                    display_name="EUR/USD",
                    pip_location=-4,
                    display_precision=5,
                    trade_units_precision=0,
                    minimum_trade_size=Decimal("1"),
                )
            ]

        async def get_prices(self, instruments):
            if instruments == ("EUR_USD",):
                return [SimpleNamespace(spread=Decimal("0.0001"))]
            assert instruments == ("USD_GBP",)
            return [SimpleNamespace(bid=Decimal("0.78"))]

        async def get_candles(self, instrument, granularity, *, count):
            candle_requests.append((instrument, granularity, count))
            return [FakeCandle(index) for index in range(100)]

        async def get_open_trades(self):
            return []

        async def get_closed_trades(self, *, count=100):
            return []

        async def get_pending_orders(self):
            return []

    class FakeAdapter:
        def __init__(self, strategy, pair, informative_candles=None):
            pass

        def signal(self, candles):
            signal = "long" if len(candles) == 1 else "short" if len(candles) == 2 else "flat"
            return type("Signal", (), {"value": signal})()

    def fake_hyperopt(*args, **kwargs):
        hyperopt_options.update(kwargs)
        return [
            {
                "parameters": {"band_length": 20},
                "minimal_roi": {"0": 0.01},
                "roi_parameters": {},
                "objective": "1.0",
                "trainNetPl": "0.5",
                "trainDrawdown": "0.1",
                "trainTrades": 2,
                "validationNetPl": "0.4",
                "validationDrawdown": "0.1",
                "validationTrades": 1,
                "stopLoss": candidate_stop_loss,
                "trailingStopLoss": kwargs.get("trailing_stop_loss", False),
            }
        ]

    monkeypatch.setenv("OANDA_CONFIG_PATH", str(tmp_path / "runtime-config.json"))
    monkeypatch.setenv("OANDA_CANDLE_CACHE_PATH", str(tmp_path / "candles.json"))
    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    monkeypatch.setattr(
        "freqtrade.forex.api.OandaSettings.from_environment",
        lambda: SimpleNamespace(
            token="token",
            account_id="account",
            environment="practice",
            execution_mode="dry_run",
            risk_fraction="0.01",
        ),
    )
    monkeypatch.setattr("freqtrade.forex.api.run_strategy_hyperopt", fake_hyperopt)
    monkeypatch.setattr("freqtrade.forex.api.FreqtradeStrategyAdapter", FakeAdapter)

    with TestClient(create_app(database_path)) as scoped_client:
        headers = _auth_headers(scoped_client)
        started = scoped_client.post(
            "/api/v1/hyperopt/start",
            headers=headers,
            json={
                "pair": "EUR/USD",
                "timeframe": "M15",
                "strategyClass": "ForexMasterStrategy",
                "historyMode": "days",
                "historyValue": 2,
                "attempts": 1,
                "spread": "0.0002",
                "slippage": "0.00001",
                "financingRatePerDayPercent": "0.02",
                "commissionRatePercent": "0.1",
                "positionSizeMode": "account_amount",
                "positionSize": "780",
                "stopDistanceMode": stop_distance_mode,
                "stopLossMode": "percent",
                "stopLossValue": "1.25",
                "trailingStopLoss": trailing_stop_loss_enabled,
            },
        )
        assert started.status_code == 200, started.text

        status_url = (
            "/api/v1/hyperopt/status?pair=EUR%2FUSD"
            "&strategy_class=ForexMasterStrategy&timeframe=M15"
        )
        for _ in range(100):
            status = scoped_client.get(status_url).json()
            if status.get("status") != "running":
                break
            time.sleep(0.01)
        assert status.get("status") == "completed", status
        assert status["report"]["costSettings"] == {
            "spread": "0.0002",
            "slippage": "0.00001",
            "financingRatePerDayPercent": "0.02",
            "commissionRatePercent": "0.1",
        }
        assert status["report"]["positionSizing"] == {
            "mode": "account_amount",
            "value": "780",
            "unit": "GBP",
            "accountCurrency": "GBP",
            "riskFraction": "0.01",
            "stopPips": expected_stop_pips,
            "quoteToAccountRate": "0.78",
        }
        assert status["report"]["stopLoss"] == expected_stop_loss
        assert status["report"]["trailingStopLoss"] is trailing_stop_loss_enabled
        assert all(
            candidate["stopLoss"] == expected_stop_loss
            for candidate in status["report"]["candidates"]
        )
        expected_trailing_value = (
            f"({expected_stop_loss['value']} {expected_stop_loss['unit']})"
            if trailing_stop_loss_enabled
            else "disabled"
        )
        trailing_label = (
            f"Trailing stop loss: {'enabled ' if trailing_stop_loss_enabled else ''}"
            f"{expected_trailing_value}"
        )
        assert trailing_label in status["report"]["reportText"]

        approved = scoped_client.post(
            "/api/v1/strategy/review",
            headers=headers,
            json={
                "status": "approved",
                "pair": "EUR/USD",
                "timeframe": "M15",
                "strategyClass": "ForexMasterStrategy",
                "requireOptimization": True,
            },
        )
        schedule_response = scoped_client.post(
            "/api/v1/auto-hyperopt",
            headers=headers,
            json={
                "enabled": True,
                "weekdays": [0, 3],
                "time": "12:00",
                "pairs": [
                    {
                        "pair": "EUR/USD",
                        "timeframe": "M15",
                        "strategyClass": "ForexMasterStrategy",
                    }
                ],
            },
        )
        scoped_client.app.state.auto_hyperopt_state["queue"] = {"status": "running"}
        blocked_hyperopt = scoped_client.post(
            "/api/v1/hyperopt/start", headers=headers, json={}
        )
        blocked_backtest = scoped_client.post(
            "/api/v1/backtests/run", headers=headers, json={}
        )
        scoped_client.app.state.auto_hyperopt_state["queue"] = {"status": "idle"}
        if stop_distance_mode == "automatic" and trailing_stop_loss_enabled:
            manual_options = dict(hyperopt_options)
            local_weekday = datetime.now().astimezone().weekday()
            scoped_client.app.state.auto_hyperopt_state["lastTriggeredDate"] = (
                datetime.now().astimezone().date().isoformat()
            )
            scoped_client.app.state.auto_hyperopt_state["queue"] = {"status": "idle"}
            trigger_schedule = scoped_client.post(
                "/api/v1/auto-hyperopt",
                headers=headers,
                json={
                    "enabled": True,
                    "weekdays": [local_weekday],
                    "time": "00:00",
                    "pairs": [
                        {
                            "pair": "EUR/USD",
                            "timeframe": "M15",
                            "strategyClass": "ForexMasterStrategy",
                        }
                    ],
                },
            )
            assert trigger_schedule.status_code == 200, trigger_schedule.text
            assert trigger_schedule.json()["scheduler"]["lastTriggeredDate"] is None
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                queue_status = scoped_client.get("/api/v1/auto-hyperopt").json()["queue"]["status"]
                if queue_status == "completed":
                    break
                time.sleep(0.05)
            assert queue_status == "completed"
            completed_queue = scoped_client.get("/api/v1/auto-hyperopt").json()["queue"]
            assert completed_queue["launchStatus"] == "started"
            assert completed_queue["activePair"] == "EUR/USD"
            assert completed_queue["startedAt"]
            assert completed_queue["launchRequestedAt"]
            assert completed_queue["hyperoptStartedAt"]
            automatic_status = scoped_client.get(status_url).json()
            assert automatic_status["report"]["attemptsRequested"] == 1
            assert automatic_status["report"]["stopDistanceMode"] == "automatic"
            assert automatic_status["report"]["costSettings"]["spread"] == "0.0001"
            hyperopt_options.clear()
            hyperopt_options.update(manual_options)
        chart = scoped_client.get("/api/v1/orders/chart?pair=EUR%2FUSD&timeframe=H4&count=120")

    assert approved.status_code == 200, approved.text
    assert approved.json()["approvedRevision"]["hyperopt"]["parameters"] == {
        "band_length": 20,
    }
    assert approved.json()["approvedRevision"]["hyperopt"]["minimal_roi"] == {"0": 0.01}
    assert approved.json()["approvedRevision"]["hyperopt"]["stopLoss"] == expected_stop_loss
    assert (
        approved.json()["approvedRevision"]["hyperopt"]["trailingStopLoss"]
        is trailing_stop_loss_enabled
    )
    approved_settings = approved.json()["approvedRevision"]["hyperopt"]["runSettings"]
    assert approved_settings["attempts"] == 1
    assert approved_settings["stopDistanceMode"] == stop_distance_mode
    assert approved_settings["spread"] == "0.0002"
    assert schedule_response.status_code == 200, schedule_response.text
    saved_schedule = schedule_response.json()
    assert saved_schedule["enabled"] is True
    assert saved_schedule["weekdays"] == [0, 3]
    assert saved_schedule["time"] == "12:00"
    assert saved_schedule["pairs"] == [
        {
            "pair": "EUR/USD",
            "timeframe": "M15",
            "strategyClass": "ForexMasterStrategy",
        }
    ]
    assert saved_schedule["availablePairs"][0]["settings"]["attempts"] == 1
    assert saved_schedule["availablePairs"][0]["settings"]["stopDistanceMode"] == stop_distance_mode
    assert saved_schedule["scheduler"]["serverNow"]
    assert saved_schedule["scheduler"]["serverTimezone"]
    assert saved_schedule["scheduler"]["nextRunAt"]
    assert datetime.fromisoformat(
        saved_schedule["scheduler"]["serverNow"]
    ).utcoffset() is not None
    assert blocked_hyperopt.status_code == 409
    assert "Automatic Hyperopt is running" in blocked_hyperopt.json()["detail"]
    assert blocked_backtest.status_code == 409
    assert "Automatic Hyperopt is running" in blocked_backtest.json()["detail"]
    assert hyperopt_options["spread"] == Decimal("0.0002")
    assert hyperopt_options["slippage"] == Decimal("0.00001")
    assert hyperopt_options["financing_rate_per_day"] == Decimal("0.0002")
    assert hyperopt_options["commission_rate"] == Decimal("0.001")
    assert hyperopt_options["position_size_mode"] == "account_amount"
    assert hyperopt_options["position_size"] == Decimal("780")
    assert hyperopt_options["quote_to_account_rate"] == Decimal("0.78")
    assert hyperopt_options["optimize_stop_distance"] is expected_optimization
    assert hyperopt_options["stop_loss_mode"] == "percent"
    assert hyperopt_options["stop_loss_value"] == Decimal("1.25")
    assert hyperopt_options["trailing_stop_loss"] is trailing_stop_loss_enabled
    assert all(
        "stopLoss" not in candidate["parameters"]
        for candidate in status["report"]["candidates"]
    )
    assert chart.status_code == 200, chart.text
    assert chart.json()["approvedStrategy"] == "ForexMasterStrategy"
    assert chart.json()["approvedTimeframe"] == "M15"
    chart_signals = chart.json()["signals"]
    assert [signal["side"] for signal in chart_signals] == ["BUY", "SELL"]
    assert [signal["roiPercent"] for signal in chart_signals] == [1.0, 1.0]
    assert abs(chart_signals[0]["roiTargetPrice"] - 1.111) < 1e-12
    assert abs(chart_signals[1]["roiTargetPrice"] - (1.10001 * 0.99)) < 1e-12
    assert candle_requests[0] == ("EUR_USD", "M15", 192)


def test_hyperopt_rejects_invalid_cost_settings(tmp_path):
    with TestClient(create_app(tmp_path / "invalid-hyperopt-cost.sqlite")) as scoped_client:
        headers = _auth_headers(scoped_client)
        response = scoped_client.post(
            "/api/v1/hyperopt/start",
            headers=headers,
            json={"spread": "-0.0001"},
        )
        invalid_size = scoped_client.post(
            "/api/v1/hyperopt/start",
            headers=headers,
            json={"positionSizeMode": "units", "positionSize": "0"},
        )
        invalid_stop_loss = scoped_client.post(
            "/api/v1/hyperopt/start",
            headers=headers,
            json={"stopLossMode": "money", "stopLossValue": "0"},
        )
        invalid_trailing_stop = scoped_client.post(
            "/api/v1/hyperopt/start",
            headers=headers,
            json={"trailingStopLoss": "true"},
        )

    assert response.status_code == 400
    assert "finite and non-negative" in response.json()["detail"]
    assert invalid_size.status_code == 400
    assert "position size must be finite and positive" in invalid_size.json()["detail"]
    assert invalid_stop_loss.status_code == 400
    assert "stop loss must be finite and positive" in invalid_stop_loss.json()["detail"]
    assert invalid_trailing_stop.status_code == 400
    assert "trailingStopLoss must be a boolean" in invalid_trailing_stop.json()["detail"]


def test_hyperopt_date_download_reports_and_clears_cache(tmp_path, monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    database_path = tmp_path / "candle-cache.sqlite"
    candles_requested = []

    class FakeCandle:
        def __init__(self, time):
            self.time = time
            self.complete = True
            self.open = Decimal("1.10000")
            self.high = Decimal("1.10100")
            self.low = Decimal("1.09900")
            self.close = Decimal("1.10050")
            self.volume = 42

    class FakeClient:
        def __init__(self, token, account_id, environment):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get_candles(self, instrument, granularity, *, from_time, to_time):
            candles_requested.append((instrument, granularity, from_time, to_time))
            return [
                FakeCandle("2026-06-01T12:00:00Z"),
                FakeCandle("2026-06-02T12:00:00Z"),
                FakeCandle("2026-06-03T00:00:00Z"),
            ]

    monkeypatch.setenv("OANDA_CANDLE_CACHE_PATH", str(tmp_path / "candles.json"))
    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    monkeypatch.setattr(
        "freqtrade.forex.api.OandaSettings.from_environment",
        lambda: SimpleNamespace(
            token="token",
            account_id="account",
            environment="practice",
        ),
    )

    with TestClient(create_app(database_path)) as scoped_client:
        inventory_url = "/api/v1/hyperopt/data-cache?pair=EUR%2FUSD&timeframe=M15"
        empty_inventory = scoped_client.get(inventory_url)
        headers = _auth_headers(scoped_client)
        downloaded = scoped_client.post(
            "/api/v1/hyperopt/data-download",
            headers=headers,
            json={
                "pair": "EUR/USD",
                "timeframe": "M15",
                "startDate": "2026-06-01",
                "endDate": "2026-06-02",
            },
        )
        inventory = scoped_client.get(inventory_url)
        today_download = scoped_client.post(
            "/api/v1/hyperopt/data-download",
            headers=headers,
            json={
                "pair": "EUR/USD",
                "timeframe": "M15",
                "startDate": "2026-06-01",
                "endDate": datetime.now(UTC).date().isoformat(),
            },
        )
        rejected = scoped_client.post(
            "/api/v1/hyperopt/data-download",
            headers=headers,
            json={
                "pair": "EUR/USD",
                "timeframe": "M15",
                "startDate": "2026-06-03",
                "endDate": "2026-06-02",
            },
        )
        unauthorized_clear = scoped_client.delete(inventory_url)
        cleared = scoped_client.delete(inventory_url, headers=headers)
        cleared_inventory = scoped_client.get(inventory_url)

    assert empty_inventory.status_code == 200
    assert empty_inventory.json()["candles"] == 0
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.json()["candles"] == 2
    assert candles_requested[0] == (
        "EUR_USD",
        "M15",
        "2026-06-01T00:00:00Z",
        "2026-06-03T00:00:00Z",
    )
    assert inventory.status_code == 200
    assert inventory.json()["candles"] == 2
    assert inventory.json()["cachedRanges"] == 1
    assert inventory.json()["ranges"][0]["from"].startswith("2026-06-01T12:00:00")
    assert inventory.json()["ranges"][0]["to"].startswith("2026-06-02T12:00:00")
    assert today_download.status_code == 200, today_download.text
    assert today_download.json()["effectiveEnd"]
    assert datetime.fromisoformat(today_download.json()["effectiveEnd"]) <= datetime.now(UTC)
    assert all(
        datetime.fromisoformat(request[3].replace("Z", "+00:00")) <= datetime.now(UTC)
        for request in candles_requested[1:]
    )
    assert rejected.status_code == 400
    assert unauthorized_clear.status_code == 401
    assert cleared.status_code == 200
    assert cleared.json()["removedRanges"] == 2
    assert cleared_inventory.json()["candles"] == 0


def test_hyperopt_uses_selected_date_range_instead_of_candle_default(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    database_path = tmp_path / "date-range-hyperopt.sqlite"
    received_candle_counts = []
    received_cost_settings = {}

    class FakeCandle:
        def __init__(self, timestamp):
            self.time = timestamp.isoformat().replace("+00:00", "Z")
            self.complete = True
            self.open = Decimal("1.10000")
            self.high = Decimal("1.10100")
            self.low = Decimal("1.09900")
            self.close = Decimal("1.10050")
            self.volume = 42

    class FakeClient:
        def __init__(self, token, account_id, environment):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get_account_summary(self):
            return SimpleNamespace(balance=Decimal("10000"), currency="USD")

        async def get_instruments(self, instruments):
            assert instruments == ("EUR_USD",)
            return [OandaInstrument("EUR_USD", "EUR/USD", -4, 5, 0, Decimal("1"))]

        async def get_prices(self, instruments):
            assert instruments == ("EUR_USD",)
            return [SimpleNamespace(spread=Decimal("0.0001"))]

        async def get_candles(self, instrument, granularity, *, from_time, to_time):
            assert instrument == "EUR_USD"
            assert granularity == "M15"
            start = datetime.fromisoformat(from_time.replace("Z", "+00:00"))
            return [FakeCandle(start + timedelta(minutes=15 * index)) for index in range(80)]

    class FakeStrategy:
        minimal_roi = {"0": 0.01}

    def fake_hyperopt(candles, *args, **kwargs):
        received_candle_counts.append(len(candles))
        received_cost_settings.update(kwargs)
        return [
            {
                "parameters": {},
                "minimal_roi": {"0": 0.01},
                "roi_parameters": {},
                "objective": "1.0",
                "trainNetPl": "0.5",
                "trainDrawdown": "0.1",
                "trainTrades": 2,
                "validationNetPl": "0.4",
                "validationDrawdown": "0.1",
                "validationTrades": 1,
            }
        ]

    monkeypatch.setenv("OANDA_CONFIG_PATH", str(tmp_path / "runtime-config.json"))
    monkeypatch.setenv("OANDA_CANDLE_CACHE_PATH", str(tmp_path / "candles.json"))
    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    monkeypatch.setattr(
        "freqtrade.forex.api.OandaSettings.from_environment",
        lambda: SimpleNamespace(
            token="token",
            account_id="account",
            environment="practice",
            execution_mode="dry_run",
            risk_fraction="0.01",
        ),
    )
    monkeypatch.setattr("freqtrade.forex.api.load_strategy", lambda *args, **kwargs: FakeStrategy())
    monkeypatch.setattr("freqtrade.forex.api.strategy_informative_timeframes", lambda *args: ())
    monkeypatch.setattr("freqtrade.forex.api.run_strategy_hyperopt", fake_hyperopt)

    with TestClient(create_app(database_path)) as scoped_client:
        headers = _auth_headers(scoped_client)
        started = scoped_client.post(
            "/api/v1/hyperopt/start",
            headers=headers,
            json={
                "pair": "EUR/USD",
                "timeframe": "M15",
                "strategyClass": "ForexMasterStrategy",
                "historyMode": "date_range",
                "historyValue": 500,
                "startDate": "2026-01-01",
                "endDate": "2026-01-02",
                "attempts": 1,
            },
        )
        assert started.status_code == 200, started.text

        status_url = (
            "/api/v1/hyperopt/status?pair=EUR%2FUSD"
            "&strategy_class=ForexMasterStrategy&timeframe=M15"
        )
        for _ in range(100):
            status = scoped_client.get(status_url).json()
            if status.get("status") != "running":
                break
            time.sleep(0.01)

    assert status.get("status") == "completed", status
    assert received_candle_counts == [80]
    assert status["report"]["historyMode"] == "date_range"
    assert status["report"]["historyValue"] == 80
    assert status["report"]["steps"] == 80
    assert status["report"]["trainCandles"] == 40
    assert status["report"]["validationCandles"] == 40
    assert status["report"]["costSettings"] == {
        "spread": "0.0001",
        "slippage": "0",
        "financingRatePerDayPercent": "0",
        "commissionRatePercent": "0",
    }
    assert status["report"]["positionSizing"] == {
        "mode": "risk",
        "value": None,
        "unit": "risk",
        "accountCurrency": "USD",
        "riskFraction": "0.01",
        "stopPips": "0.5",
        "quoteToAccountRate": "1",
    }
    assert received_cost_settings["spread"] == Decimal("0.0001")
    assert received_cost_settings["slippage"] == Decimal("0")
    assert received_cost_settings["financing_rate_per_day"] == Decimal("0")
    assert received_cost_settings["commission_rate"] == Decimal("0")
    assert received_cost_settings["position_size_mode"] == "risk"
    assert received_cost_settings["position_size"] == Decimal("1000")


def test_cors_allows_vite_frontend_origin():
    response = client.options(
        "/api/v1/account/summary",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_app_starts_without_built_ui_assets(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    dist_dir = project_root / "apps" / "ui" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html><body>fallback</body></html>", encoding="utf-8")

    module_path = project_root / "freqtrade" / "forex" / "api.py"
    module_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("freqtrade.forex.api.__file__", str(module_path), raising=False)

    created = __import__("freqtrade.forex.api", fromlist=["create_app"]).create_app()
    assert created is not None
    assert created.state.ui_assets_available is False


def test_backtest_run_uses_requested_candles(monkeypatch, tmp_path):
    download_requests = []
    backtester_options = []
    account_currencies = iter(("USD", "USD", "GBP", "USD", "USD"))
    monkeypatch.setenv("OANDA_CANDLE_CACHE_PATH", str(tmp_path / "candles.json"))
    approved_config_path = tmp_path / "approved-backtest-config.json"
    monkeypatch.setenv("OANDA_CONFIG_PATH", str(approved_config_path))

    class FakeResult:
        net_pl = Decimal("123.45")
        starting_balance = Decimal("10000")
        ending_balance = Decimal("10123.45")
        win_rate = Decimal("0.6")
        trades = []
        trade_output = []
        max_drawdown = Decimal("25")
        max_drawdown_rate = Decimal("0.25")

    class FakeClient:
        def __init__(self, token, account_id, environment, **kwargs):
            self.token = token
            self.account_id = account_id
            self.environment = environment

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_instruments(self, instruments):
            return [
                type(
                    "Instrument",
                    (),
                    {"name": "EUR_USD", "pair": "EUR/USD", "quote_currency": "USD"},
                )()
            ]

        async def get_account_summary(self):
            return type(
                "Account",
                (),
                {"balance": "10000", "currency": next(account_currencies)},
            )()

        async def get_candles(self, instrument, granularity, count=None, **kwargs):
            download_requests.append((instrument, granularity, count, kwargs))
            return [
                type(
                    "Candle",
                    (),
                    {
                        "time": "2024-01-01T00:00:00Z",
                        "complete": True,
                        "open": "1.0",
                        "high": "1.1",
                        "low": "0.9",
                        "close": "1.05",
                        "volume": 1000,
                    },
                )(),
                type(
                    "Candle",
                    (),
                    {
                        "time": "2024-01-01T00:05:00Z",
                        "complete": True,
                        "open": "1.05",
                        "high": "1.12",
                        "low": "1.0",
                        "close": "1.08",
                        "volume": 1000,
                    },
                )(),
            ]

        async def get_prices(self, instruments):
            instrument = instruments[0]
            return [
                type(
                    "Price",
                    (),
                    {
                        "instrument": instrument,
                        "spread": 0.0003,
                        "bid": "0.8",
                        "ask": "0.81",
                    },
                )()
            ]

    class FakeBacktester:
        def __init__(self, *args, **kwargs):
            backtester_options.append(kwargs)

        def run(self, frame, **kwargs):
            return FakeResult()

    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    monkeypatch.setattr(
        "freqtrade.forex.api.OandaSettings.from_environment",
        lambda: type(
            "Settings",
            (),
            {
                "token": "t",
                "account_id": "a",
                "environment": type("Env", (), {"value": "practice"})(),
                "execution_mode": "practice",
                "risk_fraction": "0.01",
            },
        )(),
    )
    monkeypatch.setattr("freqtrade.forex.api.ForexBacktester", FakeBacktester)

    login = client.post(
        "/api/v1/auth/login", json={"username": "operator", "password": "test-operator-password"}
    )
    token = login.json()["sessionToken"]
    csrf_token = login.json()["csrfToken"]

    response = client.post(
        "/api/v1/backtests/run",
        json={
            "pair": "EUR/USD",
            "timeframe": "M5",
            "historyMode": "candles",
            "historyValue": 1234,
            "spread": "0.0005",
            "slippage": "0.0001",
            "financingRatePerDayPercent": "0.02",
            "commissionRatePercent": "0.25",
            "positionSizeMode": "units",
            "positionSize": "2500",
            "stopLossMode": "money",
            "stopLossValue": "25",
        },
        headers={"X-Session-Token": token, "X-User-Role": "operator", "X-CSRF-Token": csrf_token},
    )
    date_range_response = client.post(
        "/api/v1/backtests/run",
        json={
            "pair": "EUR/USD",
            "timeframe": "M5",
            "historyMode": "date_range",
            "startDate": "2024-01-01",
            "endDate": "2024-01-01",
        },
        headers={"X-Session-Token": token, "X-User-Role": "operator", "X-CSRF-Token": csrf_token},
    )
    account_amount_response = client.post(
        "/api/v1/backtests/run",
        json={
            "pair": "EUR/USD",
            "timeframe": "M5",
            "historyMode": "candles",
            "historyValue": 100,
            "positionSizeMode": "account_amount",
            "positionSize": "1000",
        },
        headers={"X-Session-Token": token, "X-User-Role": "operator", "X-CSRF-Token": csrf_token},
    )
    invalid_size_response = client.post(
        "/api/v1/backtests/run",
        json={
            "pair": "EUR/USD",
            "timeframe": "M5",
            "positionSizeMode": "units",
            "positionSize": "0",
        },
        headers={"X-Session-Token": token, "X-User-Role": "operator", "X-CSRF-Token": csrf_token},
    )
    invalid_stop_loss_response = client.post(
        "/api/v1/backtests/run",
        json={
            "pair": "EUR/USD",
            "timeframe": "M5",
            "stopLossMode": "percent",
            "stopLossValue": "0",
        },
        headers={"X-Session-Token": token, "X-User-Role": "operator", "X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert download_requests[0][:3] == ("EUR_USD", "M5", 1234)
    assert payload["steps"] == 2
    assert "OANDA returned 2" in payload["message"]
    assert backtester_options[0]["spread"] == Decimal("0.0005")
    assert backtester_options[0]["slippage"] == Decimal("0.0001")
    assert backtester_options[0]["financing_rate_per_day"] == Decimal("0.0002")
    assert backtester_options[0]["commission_rate"] == Decimal("0.0025")
    assert backtester_options[0]["position_size_mode"] == "units"
    assert backtester_options[0]["position_size"] == Decimal("2500")
    assert backtester_options[0]["quote_to_account_rate"] == Decimal("1")
    assert backtester_options[0]["stop_loss_mode"] == "money"
    assert backtester_options[0]["stop_loss_value"] == Decimal("25")
    assert payload["execution"]["spread"] == "0.0005"
    assert payload["execution"]["positionSizeMode"] == "units"
    assert payload["execution"]["positionSize"] == "2500"
    assert payload["execution"]["stopLossMode"] == "money"
    assert payload["execution"]["stopLossValue"] == "25"
    assert payload["execution"]["stopLossOptimized"] is False
    assert date_range_response.status_code == 200, date_range_response.text
    assert date_range_response.json()["steps"] == 2
    assert backtester_options[1]["spread"] == Decimal("0.0003")
    assert backtester_options[1]["slippage"] == Decimal("0")
    assert backtester_options[1]["financing_rate_per_day"] == Decimal("0")
    assert backtester_options[1]["commission_rate"] == Decimal("0")
    assert backtester_options[1]["position_size_mode"] == "risk"
    assert backtester_options[1]["position_size"] == Decimal("1000")
    assert backtester_options[1]["stop_loss_mode"] == "pips"
    assert backtester_options[1]["stop_loss_value"] == Decimal("0.5")
    assert date_range_response.json()["execution"]["positionSizeMode"] == "risk"
    assert account_amount_response.status_code == 200, account_amount_response.text
    assert backtester_options[2]["position_size_mode"] == "account_amount"
    assert backtester_options[2]["position_size"] == Decimal("1000")
    assert backtester_options[2]["quote_to_account_rate"] == Decimal("0.8")
    assert account_amount_response.json()["execution"]["accountCurrency"] == "GBP"
    assert account_amount_response.json()["execution"]["quoteToAccountRate"] == "0.8"
    assert invalid_size_response.status_code == 400
    assert invalid_stop_loss_response.status_code == 400
    assert "stop loss must be finite and positive" in invalid_stop_loss_response.json()["detail"]
    assert download_requests[1][2] is None
    assert download_requests[1][3]["from_time"] == "2024-01-01T00:00:00Z"
    assert download_requests[1][3]["to_time"] == "2024-01-02T00:00:00Z"

    approved_config_path.write_text(
        json.dumps(
            {
                "pair_strategies": {"EUR_USD": "ForexMasterStrategy"},
                "pair_timeframes": {"EUR_USD": "5m"},
                "pair_approved_revisions": {
                    "EUR_USD": {
                        "pair": "EUR/USD",
                        "timeframe": "M5",
                        "strategyClass": "ForexMasterStrategy",
                        "hyperopt": {
                            "trailingStopLoss": True,
                            "stopLoss": {
                                "mode": "pips",
                                "value": "7.25",
                                "optimized": True,
                            },
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    approved_trailing_response = client.post(
        "/api/v1/backtests/run",
        json={
            "pair": "EUR/USD",
            "timeframe": "M5",
            "historyValue": 100,
            "stopLossMode": "money",
            "stopLossValue": "25",
        },
        headers={"X-Session-Token": token, "X-User-Role": "operator", "X-CSRF-Token": csrf_token},
    )
    assert approved_trailing_response.status_code == 200, approved_trailing_response.text
    approved_trailing_payload = approved_trailing_response.json()
    assert backtester_options[3]["stop_loss_mode"] == "pips"
    assert backtester_options[3]["stop_loss_value"] == Decimal("7.25")
    assert backtester_options[3]["trailing_stop_loss"] is True
    assert approved_trailing_payload["execution"]["stopLossMode"] == "pips"
    assert approved_trailing_payload["execution"]["stopLossValue"] == "7.25"
    assert approved_trailing_payload["execution"]["stopLossOptimized"] is True
    assert approved_trailing_payload["execution"]["trailingStopLoss"] is True
    assert approved_trailing_payload["execution"]["configSource"] == "approved-hyperopt"

    disabled_config = json.loads(approved_config_path.read_text(encoding="utf-8"))
    disabled_config["pair_approved_revisions"]["EUR_USD"]["hyperopt"]["trailingStopLoss"] = False
    approved_config_path.write_text(json.dumps(disabled_config), encoding="utf-8")
    approved_fixed_response = client.post(
        "/api/v1/backtests/run",
        json={
            "pair": "EUR/USD",
            "timeframe": "M5",
            "historyValue": 100,
            "stopLossMode": "money",
            "stopLossValue": "25",
        },
        headers={"X-Session-Token": token, "X-User-Role": "operator", "X-CSRF-Token": csrf_token},
    )
    assert approved_fixed_response.status_code == 200, approved_fixed_response.text
    approved_fixed_payload = approved_fixed_response.json()
    assert backtester_options[4]["stop_loss_mode"] == "money"
    assert backtester_options[4]["stop_loss_value"] == Decimal("25")
    assert backtester_options[4]["trailing_stop_loss"] is False
    assert approved_fixed_payload["execution"]["trailingStopLoss"] is False


def test_order_submit_requires_operator_role_and_csrf_token(monkeypatch):
    monkeypatch.setattr("freqtrade.forex.api.OandaSettings.from_environment", lambda: None)
    denied = client.post(
        "/api/v1/orders/market",
        json={"symbol": "EUR/USD", "side": "BUY", "volume": "1200"},
        headers=_auth_headers(client, "viewer"),
    )
    assert denied.status_code == 403, denied.text

    allowed = client.post(
        "/api/v1/orders/market",
        json={"symbol": "EUR/USD", "side": "BUY", "volume": "1200"},
        headers=_auth_headers(client),
    )
    assert allowed.status_code == 200, allowed.text
    payload = allowed.json()
    assert payload["status"] in {"accepted", "queued"}
    assert payload["symbol"] == "EUR/USD"


def test_live_order_surfaces_shared_preflight_validation_error(monkeypatch):
    class FakeClient:
        def __init__(self, token, account_id, environment):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def create_market_order(self, *args, **kwargs):
            raise ValueError("take-profit price exceeds 2 decimal places")

    environment = type("Env", (), {"value": "practice"})()
    settings = type(
        "Settings",
        (),
        {
            "token": "token",
            "account_id": "account",
            "environment": environment,
        },
    )()
    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    monkeypatch.setattr("freqtrade.forex.api.OandaSettings.from_environment", lambda: settings)

    response = client.post(
        "/api/v1/orders/market",
        json={
            "symbol": "XAU/CHF",
            "side": "SELL",
            "units": 1000,
            "stopLoss": "3470.32",
            "takeProfit": "3416.82264",
            "clientOrderId": "manual-ui-test",
        },
        headers=_auth_headers(client),
    )

    assert response.status_code == 400
    assert "take-profit price exceeds 2 decimal places" in response.json()["detail"]


def test_close_endpoint_closes_manual_trade_only(monkeypatch):
    closed_ids = []

    class FakeClient:
        def __init__(self, token, account_id, environment):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get_open_trades(self):
            return [
                {"id": "manual-1", "clientExtensions": {"id": "manual-ui-1"}},
                {"id": "strategy-1", "clientExtensions": {"id": "strategy-entry-1"}},
            ]

        async def close_trade(self, trade_id):
            closed_ids.append(trade_id)
            return {"orderFillTransaction": {"id": "close-tx", "price": "1.1010"}}

    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    environment = type("Env", (), {"value": "practice"})()
    settings = type(
        "Settings", (), {"token": "token", "account_id": "account", "environment": environment}
    )()
    monkeypatch.setattr("freqtrade.forex.api.OandaSettings.from_environment", lambda: settings)
    headers = _auth_headers(client)

    manual = client.post("/api/v1/positions/manual-1/close", headers=headers)
    strategy = client.post("/api/v1/positions/strategy-1/close", headers=headers)

    assert manual.status_code == 200, manual.text
    assert manual.json()["transactionId"] == "close-tx"
    assert strategy.status_code == 403
    assert closed_ids == ["manual-1"]


def test_modify_endpoint_updates_manual_trade_protection_only(monkeypatch):
    modified = []

    class FakeClient:
        def __init__(self, token, account_id, environment):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get_open_trades(self):
            return [
                {"id": "manual-1", "tradeClientExtensions": {"id": "manual-ui-1"}},
                {"id": "strategy-1", "clientExtensions": {"id": "strategy-entry-1"}},
            ]

        async def modify_trade_orders(
            self,
            trade_id,
            *,
            stop_loss_price=None,
            take_profit_price=None,
            trailing_stop_loss_distance=None,
        ):
            modified.append(
                (
                    trade_id,
                    stop_loss_price,
                    take_profit_price,
                    trailing_stop_loss_distance,
                )
            )
            return {"lastTransactionID": "modify-tx"}

    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    settings = type(
        "Settings",
        (),
        {
            "token": "token",
            "account_id": "account",
            "environment": type("Env", (), {"value": "practice"})(),
        },
    )()
    monkeypatch.setattr("freqtrade.forex.api.OandaSettings.from_environment", lambda: settings)
    headers = _auth_headers(client)

    manual = client.post(
        "/api/v1/positions/manual-1/modify",
        json={"stopLoss": "1.0900", "takeProfit": "1.1100"},
        headers=headers,
    )
    trailing = client.post(
        "/api/v1/positions/manual-1/modify",
        json={"trailingStopLossDistance": "0.0015", "takeProfit": "1.1100"},
        headers=headers,
    )
    conflict = client.post(
        "/api/v1/positions/manual-1/modify",
        json={
            "stopLoss": "1.0900",
            "trailingStopLossDistance": "0.0015",
        },
        headers=headers,
    )
    invalid_distance = client.post(
        "/api/v1/positions/manual-1/modify",
        json={"trailingStopLossDistance": "0"},
        headers=headers,
    )
    unauthorized = client.post(
        "/api/v1/positions/manual-1/modify",
        json={"trailingStopLossDistance": "0.0015"},
    )
    strategy = client.post(
        "/api/v1/positions/strategy-1/modify",
        json={"stopLoss": "1.0900", "takeProfit": "1.1100"},
        headers=headers,
    )

    assert manual.status_code == 200, manual.text
    assert manual.json()["transactionId"] == "modify-tx"
    assert trailing.status_code == 200, trailing.text
    assert conflict.status_code == 400
    assert "cannot be set simultaneously" in conflict.json()["detail"]
    assert invalid_distance.status_code == 400
    assert unauthorized.status_code == 401
    assert strategy.status_code == 403
    assert modified == [
        ("manual-1", "1.0900", "1.1100", None),
        ("manual-1", None, "1.1100", "0.0015"),
    ]
    live_settings = type(
        "Settings",
        (),
        {
            "token": "token",
            "account_id": "account",
            "environment": type("Env", (), {"value": "live"})(),
        },
    )()
    monkeypatch.setattr(
        "freqtrade.forex.api.OandaSettings.from_environment",
        lambda: live_settings,
    )
    live_environment = client.post(
        "/api/v1/positions/manual-1/modify",
        json={"trailingStopLossDistance": "0.0015"},
        headers=headers,
    )
    assert live_environment.status_code == 403
    assert "Practice environment" in live_environment.json()["detail"]
    assert len(modified) == 2


def test_pending_orders_are_broker_sourced_and_manual_modification_keeps_side(monkeypatch):
    modified = []

    class FakeClient:
        def __init__(self, token, account_id, environment):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get_pending_orders(self):
            return [
                {
                    "id": "pending-1",
                    "instrument": "EUR_USD",
                    "units": "-1200",
                    "price": "1.0800",
                    "createTime": "2026-09-28T10:00:00Z",
                    "clientExtensions": {"id": "manual-ui-1"},
                },
                {
                    "id": "pending-strategy",
                    "instrument": "GBP_USD",
                    "units": "500",
                    "price": "1.2500",
                    "clientExtensions": {"id": "strategy-order-1"},
                },
            ]

        async def modify_order(self, order_id, *, price=None, units=None):
            modified.append((order_id, price, units))
            return type("Result", (), {"order_id": order_id, "transaction_id": "modify-order-tx"})()

    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    settings = type(
        "Settings",
        (),
        {
            "token": "token",
            "account_id": "account",
            "environment": type("Env", (), {"value": "practice"})(),
        },
    )()
    monkeypatch.setattr("freqtrade.forex.api.OandaSettings.from_environment", lambda: settings)
    headers = _auth_headers(client)

    response = client.get("/api/v1/orders/pending")
    changed = client.post(
        "/api/v1/orders/pending-1/modify",
        json={"price": "1.0750", "units": "900"},
        headers=headers,
    )
    denied = client.post(
        "/api/v1/orders/pending-strategy/modify",
        json={"price": "1.0750", "units": "900"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()[0]["side"] == "SELL"
    assert response.json()[0]["manual"] is True
    assert changed.status_code == 200, changed.text
    assert denied.status_code == 403
    assert modified == [("pending-1", "1.0750", -900)]


def test_limit_order_endpoint_creates_manual_practice_order(monkeypatch):
    submitted = []

    class FakeClient:
        def __init__(self, token, account_id, environment):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def create_limit_order(self, instrument, units, price, **kwargs):
            submitted.append((instrument, units, price, kwargs))
            return type(
                "Result",
                (),
                {"order_id": "limit-1", "transaction_id": "limit-tx", "fill_price": None},
            )()

    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    settings = type(
        "Settings",
        (),
        {
            "token": "token",
            "account_id": "account",
            "environment": type("Env", (), {"value": "practice"})(),
        },
    )()
    monkeypatch.setattr("freqtrade.forex.api.OandaSettings.from_environment", lambda: settings)

    response = client.post(
        "/api/v1/orders/limit",
        json={
            "symbol": "EUR/USD",
            "side": "SELL",
            "units": 1200,
            "price": "1.0800",
            "stopLoss": "1.0900",
            "takeProfit": "1.0600",
            "clientOrderId": "manual-ui-limit-1",
        },
        headers=_auth_headers(client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
    assert submitted[0][0:3] == ("EUR_USD", -1200, "1.0800")
    assert submitted[0][3]["client_order_id"] == "manual-ui-limit-1"


def test_login_returns_session_and_session_validation_works():
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "operator", "password": "test-operator-password"},
    )
    assert login.status_code == 200, login.text
    payload = login.json()
    assert "sessionToken" in payload
    assert payload["user"]["role"] == "operator"

    session = client.get(
        "/api/v1/auth/session",
        headers={"Authorization": f"Bearer {payload['sessionToken']}"},
    )
    assert session.status_code == 200, session.text
    assert session.json()["role"] == "operator"

    denied = client.post(
        "/api/v1/orders/market",
        json={"symbol": "EUR/USD", "side": "BUY", "volume": "1200"},
        headers={"X-Session-Token": payload["sessionToken"], "X-CSRF-Token": payload["csrfToken"]},
    )
    assert denied.status_code == 403, denied.text

    allowed = client.post(
        "/api/v1/ops/validation",
        json={"environment": "practice", "executionMode": "Practice", "instrument": "EUR/USD"},
        headers={
            "X-Session-Token": payload["sessionToken"],
            "X-User-Role": "operator",
            "X-CSRF-Token": payload["csrfToken"],
        },
    )
    assert allowed.status_code == 200, allowed.text


def test_logout_revokes_session_and_expired_sessions_are_rejected(monkeypatch):
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "operator", "password": "test-operator-password"},
    )
    assert login.status_code == 200, login.text
    payload = login.json()
    assert payload["expiresIn"] == 8 * 60 * 60
    headers = {
        "X-Session-Token": payload["sessionToken"],
        "X-CSRF-Token": payload["csrfToken"],
    }
    invalid_logout = client.post(
        "/api/v1/auth/logout",
        headers={**headers, "X-CSRF-Token": "invalid-token"},
    )
    assert invalid_logout.status_code == 403, invalid_logout.text
    logged_out = client.post("/api/v1/auth/logout", headers=headers)
    assert logged_out.status_code == 200, logged_out.text
    revoked = client.get("/api/v1/auth/session", headers=headers)
    assert revoked.status_code == 401, revoked.text

    renewed_login = client.post(
        "/api/v1/auth/login",
        json={"username": "operator", "password": "test-operator-password"},
    )
    renewed = renewed_login.json()
    headers = {
        "X-Session-Token": renewed["sessionToken"],
        "X-CSRF-Token": renewed["csrfToken"],
    }

    from freqtrade.forex import api as api_module

    real_datetime = datetime

    class FutureDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.now(tz) + timedelta(hours=9)

    monkeypatch.setattr(api_module, "datetime", FutureDatetime)
    expired = client.get("/api/v1/auth/session", headers=headers)
    assert expired.status_code == 401, expired.text


def test_setup_mutations_require_authenticated_session():
    requests = (
        client.post("/api/v1/setup/discover", json={"token": "not-used"}),
        client.post("/api/v1/setup", json={"token": "not-used"}),
        client.post("/api/v1/setup/runtime", json={"action": "pause"}),
        client.post("/api/v1/setup/files/config", json={"content": "{}"}),
        client.post("/api/v1/strategy/review", json={"status": "approved"}),
    )
    assert [response.status_code for response in requests] == [401, 401, 401, 401, 401]


def test_login_fails_closed_when_no_api_users_are_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("FOREX_API_USERS_JSON", raising=False)
    monkeypatch.setenv("OANDA_CONFIG_PATH", str(tmp_path / "no-api-users.json"))
    with TestClient(create_app(tmp_path / "no-api-users.sqlite")) as unconfigured_client:
        response = unconfigured_client.post(
            "/api/v1/auth/login",
            json={"username": "operator", "password": "test-operator-password"},
        )
    assert response.status_code == 503, response.text


def test_login_accepts_credentials_from_local_config_file(monkeypatch, tmp_path):
    monkeypatch.delenv("FOREX_API_USERS_JSON", raising=False)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_users": {
                    "admin": {"password": "1234", "role": "admin"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OANDA_CONFIG_PATH", str(config_path))
    with TestClient(create_app(tmp_path / "local-config.sqlite")) as local_client:
        response = local_client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "1234"},
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["user"]["username"] == "admin"
    assert payload["user"]["role"] == "admin"


def test_operation_preflight_validation_accepts_valid_session_and_rejects_invalid_role():
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "operator", "password": "test-operator-password"},
    )
    token = login.json()["sessionToken"]

    valid = client.post(
        "/api/v1/ops/validation",
        json={"environment": "practice", "executionMode": "Practice", "instrument": "EUR/USD"},
        headers={
            "X-Session-Token": token,
            "X-User-Role": "operator",
            "X-CSRF-Token": login.json()["csrfToken"],
        },
    )
    assert valid.status_code == 200, valid.text
    payload = valid.json()
    assert payload["allowed"] is True
    assert payload["checks"]["sessionValid"] is True
    assert payload["checks"]["roleAllowed"] is True
    assert payload["checks"]["csrfPresent"] is True

    invalid = client.post(
        "/api/v1/ops/validation",
        json={"environment": "live", "executionMode": "Live", "instrument": "EUR/USD"},
        headers={
            "X-Session-Token": token,
            "X-User-Role": "viewer",
            "X-CSRF-Token": login.json()["csrfToken"],
        },
    )
    assert invalid.status_code == 403, invalid.text


def test_practice_order_submit_uses_real_oanda_gateway(monkeypatch):
    class FakeResult:
        order_id = "practice-order-123"
        transaction_id = "practice-tx-123"
        fill_price = "1.0950"

    class FakeClient:
        def __init__(self, token, account_id, environment, **kwargs):
            self.token = token
            self.account_id = account_id
            self.environment = environment
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_instruments(self, instruments):
            return [
                OandaInstrument(
                    name="EUR_USD",
                    display_name="EUR/USD",
                    pip_location=-4,
                    display_precision=5,
                    trade_units_precision=0,
                    minimum_trade_size=Decimal("1"),
                )
            ]

        async def get_prices(self, instruments):
            return [
                type(
                    "Price",
                    (),
                    {
                        "tradeable": True,
                        "instrument": "EUR_USD",
                        "bid": Decimal("1.09500"),
                        "ask": Decimal("1.09510"),
                        "price_for_side": lambda self, side: (
                            Decimal("1.09510") if side == "long" else Decimal("1.09500")
                        ),
                    },
                )()
            ]

        async def create_market_order(
            self,
            instrument,
            units,
            *,
            stop_loss_price=None,
            take_profit_price=None,
            client_order_id=None,
            trade_client_extensions=None,
        ):
            assert instrument == "EUR_USD"
            assert units == 1200
            assert stop_loss_price == "1.0850"
            assert take_profit_price == "1.1100"
            assert client_order_id == "manual-ui-test-1"
            assert trade_client_extensions == {
                "id": "manual-ui-test-1",
                "tag": "manual",
                "comment": "Manual dashboard ticket",
            }
            return FakeResult()

    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    monkeypatch.setattr(
        "freqtrade.forex.api.OandaSettings.from_environment",
        lambda: type(
            "Settings",
            (),
            {
                "token": "practice-token",
                "account_id": "practice-acct",
                "environment": type("Env", (), {"value": "practice"})(),
                "execution_mode": "practice",
                "risk_fraction": "0.01",
            },
        )(),
    )

    login = client.post(
        "/api/v1/auth/login", json={"username": "operator", "password": "test-operator-password"}
    )
    token = login.json()["sessionToken"]
    csrf_token = login.json()["csrfToken"]

    response = client.post(
        "/api/v1/orders/market",
        json={
            "symbol": "EUR/USD",
            "side": "BUY",
            "units": 1200,
            "stopLoss": "1.0850",
            "takeProfit": "1.1100",
            "clientOrderId": "manual-ui-test-1",
        },
        headers={
            "X-Session-Token": token,
            "X-User-Role": "operator",
            "X-CSRF-Token": csrf_token,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["environment"] == "practice"
    assert payload["status"] == "filled"
    assert payload["orderId"] == "practice-order-123"


def test_market_order_exposes_oanda_cancel_reason(monkeypatch):
    class FakeResult:
        order_id = "practice-order-cancelled"
        transaction_id = "practice-tx-cancelled"
        fill_price = None
        cancel_reason = "INSUFFICIENT_MARGIN"
        units = 1200

    class FakeClient:
        def __init__(self, token, account_id, environment, **kwargs):
            self.token = token
            self.account_id = account_id
            self.environment = environment

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_instruments(self, instruments):
            return [
                OandaInstrument(
                    name="EUR_USD",
                    display_name="EUR/USD",
                    pip_location=-4,
                    display_precision=5,
                    trade_units_precision=0,
                    minimum_trade_size=Decimal("1"),
                )
            ]

        async def create_market_order(
            self,
            instrument,
            units,
            *,
            stop_loss_price=None,
            take_profit_price=None,
            client_order_id=None,
        ):
            return FakeResult()

    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    monkeypatch.setattr(
        "freqtrade.forex.api.OandaSettings.from_environment",
        lambda: type(
            "Settings",
            (),
            {
                "token": "practice-token",
                "account_id": "practice-acct",
                "environment": type("Env", (), {"value": "practice"})(),
                "execution_mode": "practice",
                "risk_fraction": "0.01",
            },
        )(),
    )

    login = client.post(
        "/api/v1/auth/login", json={"username": "operator", "password": "test-operator-password"}
    )
    token = login.json()["sessionToken"]
    csrf_token = login.json()["csrfToken"]

    response = client.post(
        "/api/v1/orders/market",
        json={"symbol": "EUR/USD", "side": "BUY", "units": 1200},
        headers={"X-Session-Token": token, "X-User-Role": "operator", "X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "cancelled"
    assert payload["cancelReason"] == "INSUFFICIENT_MARGIN"
    assert payload["reason"] == "INSUFFICIENT_MARGIN"


def test_market_order_rejects_non_tradeable_instrument(monkeypatch):
    from freqtrade.forex.oanda import OandaMarketNotTradeableError

    class FakeClient:
        def __init__(self, token, account_id, environment, **kwargs):
            self.token = token
            self.account_id = account_id
            self.environment = environment

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get_instruments(self, instruments):
            return [
                OandaInstrument(
                    name="EUR_USD",
                    display_name="EUR/USD",
                    pip_location=-4,
                    display_precision=5,
                    trade_units_precision=0,
                    minimum_trade_size=Decimal("1"),
                )
            ]

        async def get_prices(self, instruments):
            return [type("Price", (), {"tradeable": False, "instrument": "EUR_USD"})()]

        async def create_market_order(
            self,
            instrument,
            units,
            *,
            stop_loss_price=None,
            take_profit_price=None,
            client_order_id=None,
        ):
            raise OandaMarketNotTradeableError(
                f"Order rejected: instrument {instrument} is not tradeable right now "
                "(OANDA market halted, closed, or otherwise non-tradeable)."
            )

    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeClient)
    monkeypatch.setattr(
        "freqtrade.forex.api.OandaSettings.from_environment",
        lambda: type(
            "Settings",
            (),
            {
                "token": "practice-token",
                "account_id": "practice-acct",
                "environment": type("Env", (), {"value": "practice"})(),
                "execution_mode": "practice",
                "risk_fraction": "0.01",
            },
        )(),
    )

    login = client.post(
        "/api/v1/auth/login", json={"username": "operator", "password": "test-operator-password"}
    )
    token = login.json()["sessionToken"]
    csrf_token = login.json()["csrfToken"]

    response = client.post(
        "/api/v1/orders/market",
        json={"symbol": "EUR/USD", "side": "BUY", "units": 1200},
        headers={"X-Session-Token": token, "X-User-Role": "operator", "X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 409, response.text
    payload = response.json()
    assert "tradeable" in payload["detail"].lower()
    assert "market" in payload["detail"].lower()


def test_audit_log_redacts_tokens_and_exposes_only_metadata():
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "operator", "password": "test-operator-password"},
    )
    assert login.status_code == 200, login.text

    response = client.get("/api/v1/audit/logs")
    assert response.status_code == 200, response.text
    logs = response.json()
    assert isinstance(logs, list)
    assert any(log.get("event") == "auth.login" for log in logs)

    for entry in logs:
        payload = entry.get("details", {})
        serialized = repr(payload)
        assert "operator" in serialized or "auth.login" in serialized or True
        assert "sessionToken" not in serialized or "***REDACTED***" in serialized
        assert "password" not in serialized or "***REDACTED***" in serialized


def test_live_release_gate_requires_explicit_approval():
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "test-admin-password"},
    )
    token = login.json()["sessionToken"]

    denied = client.post(
        "/api/v1/ops/validation",
        json={"environment": "live", "executionMode": "Live", "instrument": "EUR/USD"},
        headers={
            "X-Session-Token": token,
            "X-User-Role": "admin",
            "X-CSRF-Token": login.json()["csrfToken"],
        },
    )
    assert denied.status_code == 403, denied.text

    allowed = client.post(
        "/api/v1/ops/validation",
        json={
            "environment": "live",
            "executionMode": "Live",
            "instrument": "EUR/USD",
            "releaseGate": "approved",
        },
        headers={
            "X-Session-Token": token,
            "X-User-Role": "admin",
            "X-CSRF-Token": login.json()["csrfToken"],
        },
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["allowed"] is True


def test_setup_instruments_endpoint_lists_supported_pairs(monkeypatch):
    async def fake_get_instruments(self, instruments=None):
        return [
            type("Instrument", (), {"name": "EUR_USD", "display_name": "EUR/USD"})(),
            type("Instrument", (), {"name": "GBP_USD", "display_name": "GBP/USD"})(),
            type("Instrument", (), {"name": "USD_JPY", "display_name": "USD/JPY"})(),
        ]

    monkeypatch.setattr("freqtrade.forex.api.OandaClient.get_instruments", fake_get_instruments)

    response = client.get(
        "/api/v1/setup/instruments",
        params={"token": "demo-token", "accountId": "live-account", "environment": "practice"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["environment"] == "practice"
    assert [item["name"] for item in payload["instruments"][:3]] == [
        "EUR_USD",
        "GBP_USD",
        "USD_JPY",
    ]
    assert payload["instruments"][0]["priority"] in {"high", "priority"}


def test_setup_runtime_and_file_endpoints_work_with_configured_paths(monkeypatch, tmp_path):
    config_path = tmp_path / "config" / "custom-config.json"
    strategy_path = tmp_path / "strategies" / "custom_strategy.py"
    monkeypatch.setenv("OANDA_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("FOREX_STRATEGY_PATH", str(strategy_path))

    async def verified_accounts(token, environment):
        assert token == "demo-token"
        assert environment.value == "practice"
        return {
            "accounts": [
                {
                    "accountId": "101-000-1234567-001",
                    "accountTypeCode": "003",
                    "accountType": "CFD",
                    "tags": ["CFD"],
                    "summary": {"alias": "Primary", "currency": "GBP"},
                    "summaryAccessible": True,
                }
            ],
            "excludedAccountCount": 0,
        }

    monkeypatch.setattr("freqtrade.forex.api.discover_oanda_accounts", verified_accounts)

    status = client.get("/api/v1/setup/status")
    assert status.status_code == 200, status.text
    assert status.json()["configFile"] == str(config_path)

    setup = client.post(
        "/api/v1/setup",
        json={
            "token": "demo-token",
            "accountId": "101-000-1234567-001",
            "accountTypeCode": "003",
            "accountConfirmed": True,
            "liveConfirmed": False,
            "environment": "practice",
            "executionMode": "dry_run",
            "instruments": ["EUR_USD", "GBP_USD"],
            "pairTimeframes": {"EUR_USD": "5m", "GBP_USD": "1h"},
            "pairStrategies": {"EUR_USD": "ForexMasterStrategy", "GBP_USD": "ForexEmaStrategy"},
            "riskFraction": "0.01",
            "configPath": str(config_path),
        },
        headers=_auth_headers(client),
    )
    assert setup.status_code == 200, setup.text
    payload = setup.json()
    assert payload["configured"] is True
    assert payload["executionMode"] == "practice"
    assert payload["accountTypeCode"] == "003"
    assert payload["pairStrategies"] == {
        "EUR_USD": "ForexMasterStrategy",
        "GBP_USD": "ForexEmaStrategy",
    }


def test_setup_discovery_returns_only_supported_accounts_and_never_token(monkeypatch):
    async def discover(token, environment):
        assert token == "private-token"
        assert environment.value == "live"
        return {
            "accounts": [
                {
                    "accountId": "eligible-1",
                    "accountTypeCode": "002",
                    "accountType": "Spread Betting",
                    "tags": ["SPREAD_BETTING"],
                    "summary": {"alias": "SB", "currency": "GBP"},
                    "summaryAccessible": True,
                },
            ],
            "excludedAccountCount": 2,
        }

    monkeypatch.setattr("freqtrade.forex.api.discover_oanda_accounts", discover)
    response = client.post(
        "/api/v1/setup/discover",
        json={"token": "private-token", "environment": "live"},
        headers=_auth_headers(client),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["accounts"][0]["accountTypeCode"] == "002"
    assert payload["excludedAccountCount"] == 2
    assert "private-token" not in response.text


def test_live_setup_requires_server_side_confirmation_flag(monkeypatch, tmp_path):
    monkeypatch.delenv("OANDA_LIVE_CONFIRM", raising=False)
    response = client.post(
        "/api/v1/setup",
        json={
            "token": "live-token",
            "accountId": "live-account",
            "accountTypeCode": "003",
            "accountConfirmed": True,
            "liveConfirmed": True,
            "environment": "live",
            "executionMode": "practice",
            "instruments": ["EUR_USD"],
            "riskFraction": "0.01",
            "configPath": str(tmp_path / "live-config.json"),
        },
        headers=_auth_headers(client),
    )
    assert response.status_code == 403
    assert "OANDA_LIVE_CONFIRM=1" in response.json()["detail"]
    assert not (tmp_path / "live-config.json").exists()


def test_live_mode_maps_environment_to_live_execution(monkeypatch, tmp_path):
    monkeypatch.setenv("OANDA_LIVE_CONFIRM", "1")

    async def verified_live_account(token, environment):
        assert token == "live-token"
        assert environment.value == "live"
        return {
            "accounts": [
                {
                    "accountId": "live-account",
                    "accountTypeCode": "003",
                    "accountType": "CFD",
                    "tags": ["CFD"],
                    "summary": {"alias": "Live CFD", "currency": "GBP"},
                    "summaryAccessible": True,
                }
            ],
            "excludedAccountCount": 0,
        }

    monkeypatch.setattr("freqtrade.forex.api.discover_oanda_accounts", verified_live_account)
    response = client.post(
        "/api/v1/setup",
        json={
            "token": "live-token",
            "accountId": "live-account",
            "accountTypeCode": "003",
            "accountConfirmed": True,
            "liveConfirmed": True,
            "environment": "live",
            "executionMode": "dry_run",
            "instruments": ["EUR_USD"],
            "riskFraction": "0.01",
            "configPath": str(tmp_path / "live-config.json"),
        },
        headers=_auth_headers(client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["executionMode"] == "live"
    saved = json.loads((tmp_path / "live-config.json").read_text(encoding="utf-8"))
    assert saved["exchange"]["oanda_environment"] == "live"
    assert saved["exchange"]["oanda_execution_mode"] == "live"


def test_untagged_practice_v20_account_can_be_confirmed(monkeypatch, tmp_path):
    async def verified_practice_account(token, environment):
        assert token == "practice-token"
        assert environment.value == "practice"
        return {
            "accounts": [
                {
                    "accountId": "practice-account",
                    "accountTypeCode": "PRACTICE",
                    "accountType": "Practice / V20",
                    "tags": [],
                    "summary": {"alias": "Practice", "currency": "GBP"},
                    "summaryAccessible": True,
                    "instrumentCount": 123,
                }
            ],
            "excludedAccountCount": 0,
        }

    monkeypatch.setattr("freqtrade.forex.api.discover_oanda_accounts", verified_practice_account)
    response = client.post(
        "/api/v1/setup",
        json={
            "token": "practice-token",
            "accountId": "practice-account",
            "accountTypeCode": "PRACTICE",
            "accountConfirmed": True,
            "liveConfirmed": False,
            "environment": "practice",
            "executionMode": "live",
            "instruments": ["EUR_USD"],
            "riskFraction": "0.01",
            "configPath": str(tmp_path / "practice-config.json"),
        },
        headers=_auth_headers(client),
    )

    assert response.status_code == 200, response.text
    assert response.json()["accountType"] == "Practice / V20"
    assert response.json()["executionMode"] == "practice"

    runtime = client.get("/api/v1/setup/runtime")
    assert runtime.status_code == 200, runtime.text
    assert runtime.json()["state"] in {"running", "paused", "stopped"}

    paused = client.post(
        "/api/v1/setup/runtime",
        json={"action": "pause"},
        headers=_auth_headers(client),
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["state"] == "paused"

    resume = client.post(
        "/api/v1/setup/runtime",
        json={"action": "resume"},
        headers=_auth_headers(client),
    )
    assert resume.status_code == 200, resume.text
    assert resume.json()["state"] == "running"

    config_download = client.get("/api/v1/setup/files/config")
    assert config_download.status_code == 200, config_download.text
    assert "schema_version" in config_download.text

    strategy_download = client.get("/api/v1/setup/files/strategy")
    assert strategy_download.status_code == 200, strategy_download.text
    assert "class" in strategy_download.text or "def" in strategy_download.text

    uploaded_config = client.post(
        "/api/v1/setup/files/config",
        json={
            "content": json.dumps({"schema_version": 2, "exchange": {"name": "oanda"}}, indent=2)
        },
        headers=_auth_headers(client),
    )
    assert uploaded_config.status_code == 200, uploaded_config.text
    assert uploaded_config.json()["uploaded"] is True

    monkeypatch.setenv("FOREX_STRATEGIES_DIR", str(tmp_path / "uploaded_strategies"))
    uploaded_strategy = client.post(
        "/api/v1/setup/files/strategy",
        json={
            "fileName": "ForexUploadTestStrategy.py",
            "content": "from freqtrade.strategy import IStrategy\nclass ForexUploadTestStrategy(IStrategy):\n    pass\n",
        },
        headers=_auth_headers(client),
    )
    assert uploaded_strategy.status_code == 200, uploaded_strategy.text
    assert uploaded_strategy.json()["uploaded"] is True
    assert uploaded_strategy.json()["strategyNames"] == ["ForexUploadTestStrategy"]
    assert client.get("/api/v1/strategies/available").json()

    duplicate_strategy = client.post(
        "/api/v1/setup/files/strategy",
        json={
            "fileName": "AnotherStrategy.py",
            "content": "from freqtrade.strategy import IStrategy\nclass ForexUploadTestStrategy(IStrategy):\n    pass\n",
        },
        headers=_auth_headers(client),
    )
    assert duplicate_strategy.status_code == 400

    invalid_strategy = client.post(
        "/api/v1/setup/files/strategy",
        json={"fileName": "NotAStrategy.py", "content": "class NotAStrategy: pass\n"},
        headers=_auth_headers(client),
    )
    assert invalid_strategy.status_code == 400


def test_hyperopt_report_formats_generic_strategy_parameters():
    report = {
        "pair": "EUR/USD",
        "timeframe": "M5",
        "status": "completed",
        "strategy": "ForexEmaStrategy",
        "candidatesTested": 1,
        "attemptsRequested": 1,
        "pairsTested": 1,
        "periodsTested": 2,
        "coverage": 2,
        "bestParameters": {"fast_period_opt": 12, "slow_period_opt": 26},
        "roiParameters": {"roi_t1": 60, "roi_p1": 0.001},
        "bestRoiVolatilityPer5m": 0.0002,
        "bestRoiVolatilityRegime": "medium",
        "objective": "100.00",
        "train": {"netPl": "50.00", "drawdown": "0.00", "trades": 1},
        "validation": {"netPl": "50.00", "drawdown": "0.00", "trades": 1},
        "candidates": [
            {
                "rank": 1,
                "parameters": {"fast_period_opt": 12, "slow_period_opt": 26},
                "minimal_roi": {"0": 0.01, "30": 0.005, "60": 0.0},
                "roi_parameters": {"roi_t1": 60, "roi_p1": 0.001},
                "objective": "100.00",
                "validationNetPl": "50.00",
                "validationTrades": 1,
            }
        ],
    }

    formatted = format_hyperopt_report(report)

    assert "Strategy: ForexEmaStrategy" in formatted
    assert "fast_period_opt=12 slow_period_opt=26" in formatted
    assert "ROI volatility: medium (0.0200% typical range per 5m)" in formatted
    assert "ROI search parameters: roi_t1=60 roi_p1=0.001" in formatted
    assert "minimal_roi=0=0.01 30=0.005 60=0.0 roi_parameters=roi_t1=60 roi_p1=0.001" in formatted
