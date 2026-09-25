"""OANDA transaction events and idempotent order lifecycle state."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any


class BrokerOrderStatus(StrEnum):
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


@dataclass(frozen=True)
class OandaTransaction:
    transaction_id: str
    transaction_type: str
    order_id: str | None
    instrument: str | None
    units: Decimal | None
    price: Decimal | None
    reason: str | None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OandaTransaction":
        units = payload.get("units")
        price = payload.get("price")
        return cls(
            transaction_id=str(payload["id"]),
            transaction_type=str(payload["type"]),
            order_id=str(payload["orderID"]) if payload.get("orderID") else None,
            instrument=payload.get("instrument"),
            units=Decimal(units) if units is not None else None,
            price=Decimal(price) if price is not None else None,
            reason=payload.get("reason"),
        )


@dataclass(frozen=True)
class OrderLifecycle:
    order_id: str
    status: BrokerOrderStatus
    last_transaction_id: str
    instrument: str | None = None
    units: Decimal | None = None
    filled_units: Decimal | None = None
    fill_price: Decimal | None = None
    reason: str | None = None


class OrderStateMachine:
    """Apply OANDA events once and retain the latest state per order."""

    _STATUS_BY_TRANSACTION = {
        "ORDER_CREATE": BrokerOrderStatus.OPEN,
        "ORDER_FILL": BrokerOrderStatus.FILLED,
        "ORDER_CANCEL": BrokerOrderStatus.CANCELED,
        "ORDER_REJECT": BrokerOrderStatus.REJECTED,
        "ORDER_CANCEL_REJECT": BrokerOrderStatus.OPEN,
    }

    def __init__(self) -> None:
        self._orders: dict[str, OrderLifecycle] = {}
        self._seen_transactions: set[str] = set()

    def apply(self, transaction: OandaTransaction) -> OrderLifecycle | None:
        if transaction.transaction_id in self._seen_transactions:
            return self._orders.get(transaction.order_id or "")
        self._seen_transactions.add(transaction.transaction_id)
        if transaction.order_id is None:
            return None
        status = self._STATUS_BY_TRANSACTION.get(transaction.transaction_type)
        if status is None:
            return self._orders.get(transaction.order_id)
        current = self._orders.get(transaction.order_id)
        filled_units = current.filled_units if current else None
        if transaction.transaction_type == "ORDER_FILL" and transaction.units is not None:
            filled_units = (filled_units or Decimal("0")) + transaction.units
        requested_units = transaction.units if current is None else current.units
        if transaction.transaction_type == "ORDER_FILL" and current is not None:
            status = (
                BrokerOrderStatus.FILLED
                if filled_units is not None
                and requested_units is not None
                and abs(filled_units) >= abs(requested_units)
                else BrokerOrderStatus.PARTIALLY_FILLED
            )
        lifecycle = OrderLifecycle(
            order_id=transaction.order_id,
            status=status,
            last_transaction_id=transaction.transaction_id,
            instrument=transaction.instrument or (current.instrument if current else None),
            units=requested_units,
            filled_units=filled_units,
            fill_price=transaction.price if transaction.price is not None else (current.fill_price if current else None),
            reason=transaction.reason or (current.reason if current else None),
        )
        self._orders[transaction.order_id] = lifecycle
        return lifecycle

    def get(self, order_id: str) -> OrderLifecycle | None:
        return self._orders.get(order_id)

    def snapshot(self) -> tuple[OrderLifecycle, ...]:
        return tuple(self._orders.values())
