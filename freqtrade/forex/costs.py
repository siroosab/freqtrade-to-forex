"""FX fill and trading-cost contracts for backtest and paper execution."""

from dataclasses import dataclass
from decimal import Decimal

from freqtrade.forex.models import OandaPrice


@dataclass(frozen=True)
class FillResult:
    requested_units: int
    filled_units: int
    unfilled_units: int
    fill_price: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal

    @property
    def is_partial(self) -> bool:
        return self.filled_units != 0 and self.unfilled_units != 0


@dataclass(frozen=True)
class ForexFillModel:
    fill_ratio: Decimal = Decimal("1")
    slippage: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not Decimal("0") < self.fill_ratio <= Decimal("1"):
            raise ValueError("fill_ratio must be greater than 0 and at most 1")
        if self.slippage < 0:
            raise ValueError("slippage must be non-negative")

    def fill(self, *, requested_units: int, price: OandaPrice) -> FillResult:
        if requested_units == 0:
            raise ValueError("requested_units cannot be zero")
        requested_abs = abs(requested_units)
        filled_abs = int((Decimal(requested_abs) * self.fill_ratio).to_integral_value())
        if filled_abs == 0:
            raise ValueError("fill ratio produces no whole units")
        filled_units = filled_abs if requested_units > 0 else -filled_abs
        unfilled_units = requested_units - filled_units
        if requested_units > 0:
            fill_price = price.ask + self.slippage
        else:
            fill_price = price.bid - self.slippage
        spread_cost = price.spread * Decimal(filled_abs)
        slippage_cost = self.slippage * Decimal(filled_abs)
        return FillResult(
            requested_units=requested_units,
            filled_units=filled_units,
            unfilled_units=unfilled_units,
            fill_price=fill_price,
            spread_cost=spread_cost,
            slippage_cost=slippage_cost,
        )


def financing_cost(
    *,
    entry_price: Decimal,
    units: int,
    days: Decimal,
    rate_per_day: Decimal,
    quote_to_account_rate: Decimal = Decimal("1"),
    direction: str | None = None,
) -> Decimal:
    if entry_price <= 0 or units == 0:
        raise ValueError("entry price must be positive and units cannot be zero")
    if days < 0 or rate_per_day < 0 or quote_to_account_rate <= 0:
        raise ValueError("days, financing rate, and conversion rate must be valid")
    notional = entry_price * Decimal(abs(units)) * quote_to_account_rate
    cost = notional * rate_per_day * days
    if direction is not None:
        side = Decimal("-1") if str(direction).lower() == "short" else Decimal("1")
        return cost * side
    return cost
