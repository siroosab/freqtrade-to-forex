"""Risk-budget and currency-correlation exposure contracts for FX."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping


class RiskLimitError(ValueError):
    """Raised when a risk budget or currency exposure limit is exceeded."""


@dataclass(frozen=True)
class RiskLimits:
    max_daily_loss: Decimal
    max_total_risk: Decimal
    max_currency_exposure: Decimal

    def __post_init__(self) -> None:
        if self.max_daily_loss <= 0 or self.max_total_risk <= 0 or self.max_currency_exposure <= 0:
            raise RiskLimitError("risk limits must be positive")


@dataclass(frozen=True)
class RiskUsage:
    daily_loss: Decimal
    total_open_risk: Decimal
    currency_exposure: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        if self.daily_loss < 0 or self.total_open_risk < 0:
            raise RiskLimitError("risk usage cannot be negative")
        if any(value < 0 for value in self.currency_exposure.values()):
            raise RiskLimitError("currency exposure cannot be negative")

    @property
    def gross_correlation_exposure(self) -> Decimal:
        return sum(self.currency_exposure.values(), Decimal("0"))


@dataclass(frozen=True)
class RiskLimitPolicy:
    limits: RiskLimits

    def validate(self, usage: RiskUsage) -> None:
        if usage.daily_loss > self.limits.max_daily_loss:
            raise RiskLimitError("maximum daily loss exceeded")
        if usage.total_open_risk > self.limits.max_total_risk:
            raise RiskLimitError("maximum total risk exceeded")
        if any(
            exposure > self.limits.max_currency_exposure
            for exposure in usage.currency_exposure.values()
        ):
            raise RiskLimitError("maximum currency correlation exposure exceeded")


@dataclass
class RiskControl:
    """Mutable pre-order risk guard with an explicit emergency kill switch."""

    policy: RiskLimitPolicy
    usage: RiskUsage
    kill_switch_active: bool = False

    def activate_kill_switch(self) -> None:
        self.kill_switch_active = True

    def deactivate_kill_switch(self) -> None:
        self.kill_switch_active = False

    def update_usage(self, usage: RiskUsage) -> None:
        self.usage = usage

    def validate(self) -> None:
        if self.kill_switch_active:
            raise RiskLimitError("kill switch is active")
        self.policy.validate(self.usage)


def aggregate_currency_exposure(
    exposures: Mapping[str, Decimal],
    additional: Mapping[str, Decimal] | None = None,
) -> dict[str, Decimal]:
    """Aggregate gross currency exposure conservatively by currency code."""
    result = {currency.upper(): value for currency, value in exposures.items()}
    for currency, value in (additional or {}).items():
        if value < 0:
            raise RiskLimitError("currency exposure cannot be negative")
        key = currency.upper()
        result[key] = result.get(key, Decimal("0")) + value
    return result
