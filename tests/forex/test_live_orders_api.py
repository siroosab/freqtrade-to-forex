from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from freqtrade.forex.api import create_app
from freqtrade.forex.models import OandaEnvironment
from freqtrade.forex.oanda import (
    ORDER_REJECTION_TRANSACTION_TYPES,
    ORDER_TRANSACTION_FILTERS,
    OandaClient,
)
from freqtrade.forex.transactions import (
    BrokerOrderStatus,
    OandaTransaction,
    OrderStateMachine,
)


@pytest.mark.asyncio
async def test_oanda_transaction_stream_uses_valid_order_filter() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/transactions/stream")
        assert request.url.params["type"] == "ORDER"
        return httpx.Response(
            200,
            content=b'{"id":"11","type":"MARKET_ORDER","orderID":"9","units":"1000"}\n',
        )

    async with httpx.AsyncClient(
        base_url=OandaEnvironment.PRACTICE.rest_url,
        transport=httpx.MockTransport(handler),
    ) as http_client:
        async with OandaClient("token", "account", http_client=http_client) as client:
            transactions = [transaction async for transaction in client.iter_transactions()]

    assert [transaction.transaction_type for transaction in transactions] == ["MARKET_ORDER"]


def test_order_state_machine_maps_real_oanda_order_types() -> None:
    machine = OrderStateMachine()
    opened = machine.apply(
        OandaTransaction.from_payload(
            {"id": "1", "type": "MARKET_ORDER", "orderID": "9", "units": "1000"}
        )
    )
    rejected = machine.apply(
        OandaTransaction.from_payload(
            {
                "id": "2",
                "type": "MARKET_ORDER_REJECT",
                "orderID": "10",
                "reason": "INSUFFICIENT_MARGIN",
            }
        )
    )

    assert opened is not None and opened.status is BrokerOrderStatus.OPEN
    assert rejected is not None and rejected.status is BrokerOrderStatus.REJECTED


@pytest.mark.asyncio
async def test_oanda_client_fetches_latest_bounded_order_transactions() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/transactions"):
            return httpx.Response(
                200,
                json={
                    "pages": [
                        "https://api-fxpractice.oanda.com/v3/accounts/account/transactions/idrange?from=1&to=10",
                        "https://api-fxpractice.oanda.com/v3/accounts/account/transactions/idrange?from=11&to=20",
                    ]
                },
            )
        return httpx.Response(
            200,
            json={"transactions": [{"id": "20", "type": "ORDER_CANCEL", "orderID": "9"}]},
        )

    async with httpx.AsyncClient(
        base_url=OandaEnvironment.PRACTICE.rest_url,
        transport=httpx.MockTransport(handler),
    ) as http_client:
        async with OandaClient("token", "account", http_client=http_client) as client:
            transactions = await client.get_order_transactions(count=25)
            await client.get_order_transactions(
                count=10,
                transaction_types=("MARKET_ORDER_REJECT",),
            )

    assert transactions[0]["id"] == "20"
    assert len(requests) == 4
    params = dict(requests[0].url.params)
    assert params["pageSize"] == "25"
    assert params["type"] == ",".join(ORDER_TRANSACTION_FILTERS)
    assert "ORDER_REJECT" not in params["type"].split(",")
    assert "MARKET_ORDER_REJECT" in params["type"]
    assert "from" in params and "to" in params
    assert dict(requests[1].url.params) == {
        "from": "11",
        "to": "20",
        "type": ",".join(ORDER_TRANSACTION_FILTERS),
    }
    rejected_params = dict(requests[2].url.params)
    assert rejected_params["pageSize"] == "10"
    assert rejected_params["type"] == "MARKET_ORDER_REJECT"
    assert dict(requests[3].url.params)["type"] == "MARKET_ORDER_REJECT"


def test_orders_endpoint_returns_broker_trades_and_order_events(monkeypatch, tmp_path) -> None:
    requested_transaction_types = []

    class FakeOandaClient:
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
                    "units": "1000",
                    "createTime": "2026-10-07T12:00:00Z",
                }
            ]

        async def get_open_trades(self):
            return [
                {
                    "id": "trade-open",
                    "instrument": "EUR_USD",
                    "currentUnits": "1200",
                    "openTime": "2026-10-07T11:00:00Z",
                    "unrealizedPL": "12.34",
                }
            ]

        async def get_closed_trades(self, *, count=100):
            return [
                {
                    "id": "trade-closed",
                    "instrument": "GBP_USD",
                    "initialUnits": "-500",
                    "closeTime": "2026-10-07T10:00:00Z",
                    "realizedPL": "-4.50",
                }
            ]

        async def get_order_transactions(self, *, count=100, transaction_types=None):
            requested_transaction_types.append(transaction_types)
            transactions = [
                {
                    "id": "tx-open-trade",
                    "type": "ORDER_FILL",
                    "orderID": "opening-order",
                    "instrument": "EUR_USD",
                    "units": "1200",
                    "time": "2026-10-07T11:00:00Z",
                    "tradeOpened": {"tradeID": "trade-open"},
                },
                {
                    "id": "tx-closed-trade",
                    "type": "ORDER_FILL",
                    "orderID": "closing-order",
                    "instrument": "GBP_USD",
                    "units": "500",
                    "time": "2026-10-07T10:00:00Z",
                    "tradesClosed": [{"tradeID": "trade-closed"}],
                },
                {
                    "id": "tx-fill",
                    "type": "ORDER_FILL",
                    "orderID": "filled-1",
                    "instrument": "USD_JPY",
                    "units": "-300",
                    "time": "2026-10-07T09:00:00Z",
                    "pl": "2.00",
                },
                {
                    "id": "tx-cancel",
                    "type": "ORDER_CANCEL",
                    "orderID": "cancelled-1",
                    "instrument": "EUR_USD",
                    "units": "200",
                    "time": "2026-10-07T08:00:00Z",
                    "reason": "CLIENT_REQUEST",
                },
                {
                    "id": "tx-reject",
                    "type": "MARKET_ORDER_REJECT",
                    "orderID": "rejected-1",
                    "instrument": "GBP_USD",
                    "units": "-100",
                    "time": "2026-10-07T07:00:00Z",
                    "reason": "INSUFFICIENT_MARGIN",
                },
            ]
            return [
                transaction
                for transaction in transactions
                if transaction_types is None or transaction["type"] in transaction_types
            ]

        async def get_account_summary(self):
            return SimpleNamespace(currency="USD")

    monkeypatch.setattr("freqtrade.forex.api.OandaSettings.from_environment", lambda: SimpleNamespace(
        token="token",
        account_id="account",
        environment=OandaEnvironment.PRACTICE,
    ))
    monkeypatch.setattr("freqtrade.forex.api.OandaClient", FakeOandaClient)

    with TestClient(create_app(tmp_path / "orders.sqlite")) as client:
        response = client.get("/api/v1/orders?status=all&limit=50")

    assert response.status_code == 200, response.text
    payload = response.json()
    orders = payload["orders"]
    assert [order["status"] for order in orders] == [
        "Pending",
        "Open",
        "Filled",
        "Closed",
        "Filled",
        "Filled",
        "Cancelled",
        "Rejected",
    ]
    assert len(orders) == 8
    assert next(order for order in orders if order["id"] == "trade-open")["pnl"] == "12.34"
    assert next(order for order in orders if order["id"] == "trade-closed")["pnl"] == "-4.50"
    assert next(order for order in orders if order["id"] == "filled-1")["pnl"] == "2.00"
    assert (
        next(order for order in orders if order.get("transactionId") == "tx-open-trade")["pnl"]
        == "12.34"
    )
    assert (
        next(order for order in orders if order["status"] == "Rejected")["reason"]
        == "INSUFFICIENT_MARGIN"
    )
    assert payload["historyDays"] == 365

    with TestClient(create_app(tmp_path / "filtered-orders.sqlite")) as client:
        filtered = client.get("/api/v1/orders?status=cancelled&limit=1")
    assert filtered.status_code == 200, filtered.text
    assert [order["status"] for order in filtered.json()["orders"]] == ["Cancelled"]
    assert filtered.json()["limit"] == 1

    with TestClient(create_app(tmp_path / "rejected-orders.sqlite")) as client:
        rejected = client.get("/api/v1/orders?status=rejected&limit=25")
    assert rejected.status_code == 200, rejected.text
    assert [order["status"] for order in rejected.json()["orders"]] == ["Rejected"]
    assert rejected.json()["orders"][0]["id"] == "rejected-1"
    assert requested_transaction_types[-1] == ORDER_REJECTION_TRANSACTION_TYPES
