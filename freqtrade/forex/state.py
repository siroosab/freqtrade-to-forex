"""Account and net-position state returned by OANDA."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class OandaAccountState:
    account_id: str
    currency: str
    balance: Decimal
    nav: Decimal
    margin_available: Decimal
    unrealized_pl: Decimal

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OandaAccountState":
        account = payload["account"]
        return cls(
            account_id=account["id"],
            currency=account["currency"],
            balance=Decimal(account["balance"]),
            nav=Decimal(account["NAV"]),
            margin_available=Decimal(account["marginAvailable"]),
            unrealized_pl=Decimal(account["unrealizedPL"]),
        )


@dataclass(frozen=True)
class OandaPosition:
    instrument: str
    net_units: Decimal
    long_units: Decimal
    short_units: Decimal
    average_price: Decimal | None
    unrealized_pl: Decimal
    mode: str = "netting"

    @property
    def is_hedged(self) -> bool:
        return self.long_units != Decimal("0") and self.short_units != Decimal("0")

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OandaPosition":
        long_leg = payload.get("long", {})
        short_leg = payload.get("short", {})
        long_units = Decimal(long_leg.get("units", "0"))
        short_units = Decimal(short_leg.get("units", "0"))
        average_prices = [
            Decimal(leg["averagePrice"])
            for leg in (long_leg, short_leg)
            if leg.get("averagePrice")
        ]
        mode = payload.get("mode", "netting")
        return cls(
            instrument=payload["instrument"],
            net_units=long_units + short_units,
            long_units=long_units,
            short_units=short_units,
            average_price=average_prices[0] if average_prices else None,
            unrealized_pl=Decimal(payload.get("unrealizedPL", "0")),
            mode=str(mode),
        )
