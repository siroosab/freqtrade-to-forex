"""Broker-specific FX price, unit, and protective-order validation."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from freqtrade.forex.models import OandaInstrument

Side = Literal["long", "short"]


class OrderValidationError(ValueError):
    """Raised when an order violates broker instrument constraints."""


@dataclass(frozen=True)
class ProtectiveStopPolicy:
    require_stop: bool = True

    def validate(
        self, *, stop_loss_price: str | None, no_stop_reason: str | None = None
    ) -> None:
        if stop_loss_price is not None:
            return
        if self.require_stop and not no_stop_reason:
            raise OrderValidationError(
                "protective stop is required or an explicit no-stop reason must be provided"
            )
        if not self.require_stop and not no_stop_reason:
            raise OrderValidationError("orders without a stop require an explicit policy reason")


@dataclass(frozen=True)
class BrokerOrderValidator:
    instrument: OandaInstrument
    minimum_stop_distance: Decimal

    def __post_init__(self) -> None:
        if self.minimum_stop_distance < 0:
            raise OrderValidationError("minimum stop distance cannot be negative")

    def validate_units(self, units: int) -> int:
        if units == 0:
            raise OrderValidationError("order units cannot be zero")
        absolute_units = Decimal(abs(units))
        if absolute_units < self.instrument.minimum_trade_size:
            raise OrderValidationError("order units are below minimum trade size")
        if self.instrument.round_units(absolute_units) != absolute_units:
            raise OrderValidationError("order units do not match instrument precision")
        return units

    def validate_price(self, price: Decimal, field: str = "price") -> Decimal:
        if price <= 0:
            raise OrderValidationError(f"{field} must be positive")
        precision = Decimal(1).scaleb(-self.instrument.display_precision)
        if price.quantize(precision) != price:
            raise OrderValidationError(
                f"{field} exceeds {self.instrument.display_precision} decimal places"
            )
        return price

    def validate_stop(self, *, side: Side, entry_price: Decimal, stop_price: Decimal) -> Decimal:
        self.validate_price(entry_price, "entry price")
        self.validate_price(stop_price, "stop price")
        distance = abs(entry_price - stop_price)
        if distance < self.minimum_stop_distance:
            raise OrderValidationError("stop distance is below broker minimum")
        if side == "long" and stop_price >= entry_price:
            raise OrderValidationError("long stop must be below entry price")
        if side == "short" and stop_price <= entry_price:
            raise OrderValidationError("short stop must be above entry price")
        return stop_price

    def validate_take_profit(
        self, *, side: Side, entry_price: Decimal, take_profit_price: Decimal
    ) -> Decimal:
        self.validate_price(entry_price, "entry price")
        self.validate_price(take_profit_price, "take-profit price")
        if side == "long" and take_profit_price <= entry_price:
            raise OrderValidationError("long take-profit must be above entry price")
        if side == "short" and take_profit_price >= entry_price:
            raise OrderValidationError("short take-profit must be below entry price")
        return take_profit_price
