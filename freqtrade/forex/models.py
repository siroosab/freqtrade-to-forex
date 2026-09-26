"""Domain models for OANDA v20 responses."""

from dataclasses import dataclass
from datetime import UTC, datetime, time as dt_time
from decimal import Decimal, ROUND_DOWN
from enum import StrEnum
from typing import Any


class OandaEnvironment(StrEnum):
    PRACTICE = "practice"
    LIVE = "live"

    @property
    def rest_url(self) -> str:
        return {
            OandaEnvironment.PRACTICE: "https://api-fxpractice.oanda.com",
            OandaEnvironment.LIVE: "https://api-fxtrade.oanda.com",
        }[self]

    @property
    def stream_url(self) -> str:
        return {
            OandaEnvironment.PRACTICE: "https://stream-fxpractice.oanda.com",
            OandaEnvironment.LIVE: "https://stream-fxtrade.oanda.com",
        }[self]


@dataclass(frozen=True)
class OandaInstrument:
    name: str
    display_name: str
    pip_location: int
    display_precision: int
    trade_units_precision: int
    minimum_trade_size: Decimal
    base_currency: str | None = None
    quote_currency: str | None = None

    def __post_init__(self) -> None:
        if self.name:
            candidate = self.name.replace("/", "_").replace("-", "_").upper()
            parts = [part for part in candidate.split("_") if part]
            if len(parts) >= 2:
                base = self.base_currency or parts[0]
                quote = self.quote_currency or parts[1]
                object.__setattr__(self, "base_currency", base.upper())
                object.__setattr__(self, "quote_currency", quote.upper())
        if self.base_currency is not None:
            object.__setattr__(self, "base_currency", self.base_currency.upper())
        if self.quote_currency is not None:
            object.__setattr__(self, "quote_currency", self.quote_currency.upper())

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OandaInstrument":
        name = payload["name"]
        base_currency = payload.get("baseCurrency") or payload.get("base_currency")
        quote_currency = payload.get("quoteCurrency") or payload.get("quote_currency")
        if base_currency is None or quote_currency is None:
            pieces = [part for part in name.replace("/", "_").replace("-", "_").split("_") if part]
            if len(pieces) >= 2:
                base_currency = base_currency or pieces[0]
                quote_currency = quote_currency or pieces[1]
        return cls(
            name=name,
            display_name=payload.get("displayName", name),
            pip_location=int(payload["pipLocation"]),
            display_precision=int(payload["displayPrecision"]),
            trade_units_precision=int(payload["tradeUnitsPrecision"]),
            minimum_trade_size=Decimal(payload["minimumTradeSize"]),
            base_currency=base_currency,
            quote_currency=quote_currency,
        )

    @property
    def pip_size(self) -> Decimal:
        return Decimal(10) ** self.pip_location

    @property
    def pipette_size(self) -> Decimal:
        return Decimal(10) ** (self.pip_location - 1)

    @property
    def units_step(self) -> Decimal:
        return Decimal(1).scaleb(-self.trade_units_precision)

    def round_units(self, units: Decimal) -> Decimal:
        if units >= 0:
            return units.quantize(self.units_step, rounding=ROUND_DOWN)
        return (-units).quantize(self.units_step, rounding=ROUND_DOWN) * Decimal("-1")

    def is_quote_currency(self, currency: str) -> bool:
        return self.quote_currency == currency.upper()


@dataclass(frozen=True)
class ForexCurrency:
    code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", self.code.upper())


@dataclass(frozen=True)
class ForexQuoteRate:
    base_currency: str
    quote_currency: str
    rate: Decimal
    timestamp: str
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_currency", self.base_currency.upper())
        object.__setattr__(self, "quote_currency", self.quote_currency.upper())

    def convert(self, amount: Decimal, from_currency: str, to_currency: str) -> Decimal:
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()
        if from_currency == self.base_currency and to_currency == self.quote_currency:
            return amount * self.rate
        if from_currency == self.quote_currency and to_currency == self.base_currency:
            return amount / self.rate
        raise ValueError(
            f"unsupported conversion {from_currency}->{to_currency} for rate {self.base_currency}->{self.quote_currency}"
        )


@dataclass(frozen=True)
class ForexQuote:
    base_currency: str
    quote_currency: str
    bid: Decimal
    ask: Decimal
    timestamp: str
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_currency", self.base_currency.upper())
        object.__setattr__(self, "quote_currency", self.quote_currency.upper())

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")

    def convert_amount(self, amount: Decimal, from_currency: str, to_currency: str) -> Decimal:
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()
        if from_currency == self.base_currency and to_currency == self.quote_currency:
            return amount * self.ask
        if from_currency == self.quote_currency and to_currency == self.base_currency:
            return amount / self.bid
        raise ValueError(
            f"unsupported conversion {from_currency}->{to_currency} for quote {self.base_currency}/{self.quote_currency}"
        )


@dataclass(frozen=True)
class ForexAccount:
    id: str
    currency: ForexCurrency
    balance: Decimal
    margin_available: Decimal
    unrealized_pl: Decimal


@dataclass(frozen=True)
class ForexCandle:
    time: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    complete: bool = True

    @property
    def is_complete(self) -> bool:
        return self.complete

    @property
    def time_utc(self) -> datetime:
        return datetime.fromisoformat(self.time.replace("Z", "+00:00")).astimezone(UTC)


@dataclass(frozen=True)
class ForexMarketSession:
    name: str
    open_utc: str
    close_utc: str
    timezone: str = "UTC"

    def _parse_clock(self, value: str) -> dt_time:
        return dt_time.fromisoformat(value)

    def is_open_at(self, moment: str | datetime) -> bool:
        if isinstance(moment, str):
            parsed = datetime.fromisoformat(moment.replace("Z", "+00:00")).astimezone(UTC)
        else:
            parsed = moment.astimezone(UTC)
        open_time = self._parse_clock(self.open_utc)
        close_time = self._parse_clock(self.close_utc)
        now_time = parsed.timetz().replace(tzinfo=UTC)
        start = open_time.replace(tzinfo=UTC)
        end = close_time.replace(tzinfo=UTC)
        return start <= now_time < end


@dataclass(frozen=True)
class OandaPrice:
    instrument: str
    time: str
    bid: Decimal
    ask: Decimal
    tradeable: bool = True

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid

    @property
    def midpoint(self) -> Decimal:
        return (self.bid + self.ask) / Decimal(2)

    def price_for_side(self, side: str) -> Decimal:
        normalized = side.lower()
        if normalized == "long":
            return self.ask
        if normalized == "short":
            return self.bid
        raise ValueError(f"Unsupported FX side: {side}")

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OandaPrice":
        bid = payload["bids"][0]["price"]
        ask = payload["asks"][0]["price"]
        return cls(
            instrument=payload["instrument"],
            time=payload["time"],
            bid=Decimal(bid),
            ask=Decimal(ask),
            tradeable=bool(payload.get("tradeable", True)),
        )


@dataclass(frozen=True)
class OandaCandle:
    time: str
    complete: bool
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OandaCandle":
        price = payload.get("mid", payload.get("bid"))
        if price is None:
            raise ValueError("OANDA candle has no mid or bid price")
        return cls(
            time=payload["time"],
            complete=bool(payload["complete"]),
            open=Decimal(price["o"]),
            high=Decimal(price["h"]),
            low=Decimal(price["l"]),
            close=Decimal(price["c"]),
            volume=int(payload["volume"]),
        )


@dataclass(frozen=True)
class OandaOrderResult:
    order_id: str
    transaction_id: str
    fill_price: Decimal | None
    units: Decimal
    cancel_reason: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OandaOrderResult":
        created = payload["orderCreateTransaction"]
        filled = payload.get("orderFillTransaction")
        canceled = payload.get("orderCancelTransaction")
        return cls(
            order_id=created["id"],
            transaction_id=payload["lastTransactionID"],
            fill_price=Decimal(filled["price"]) if filled else None,
            units=Decimal(created["units"]),
            cancel_reason=(canceled or {}).get("reason") if canceled else None,
        )


@dataclass(frozen=True)
class OandaOrderActionResult:
    order_id: str
    transaction_id: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OandaOrderActionResult":
        created = payload.get("orderCreateTransaction")
        canceled = payload.get("orderCancelTransaction")
        order_id = (
            (created or {}).get("id")
            or (created or {}).get("orderID")
            or (canceled or {}).get("orderID")
        )
        if order_id is None:
            raise ValueError("OANDA order action has no order id")
        return cls(order_id=str(order_id), transaction_id=str(payload["lastTransactionID"]))
