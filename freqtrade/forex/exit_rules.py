"""Composable stop, target, trailing, and time-exit contracts for FX."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

Side = Literal["long", "short"]


@dataclass(frozen=True)
class ExitLevels:
    stop_price: Decimal | None = None
    take_profit_price: Decimal | None = None


class ExitRule(Protocol):
    def levels(self, *, side: Side, entry_price: Decimal) -> ExitLevels: ...


@dataclass(frozen=True)
class FixedStop:
    distance: Decimal

    def __post_init__(self) -> None:
        if self.distance <= 0:
            raise ValueError("fixed stop distance must be positive")

    def levels(self, *, side: Side, entry_price: Decimal) -> ExitLevels:
        _validate_price(entry_price)
        stop = entry_price - self.distance if side == "long" else entry_price + self.distance
        return ExitLevels(stop_price=stop)


@dataclass(frozen=True)
class AtrStop:
    multiplier: Decimal

    def __post_init__(self) -> None:
        if self.multiplier <= 0:
            raise ValueError("ATR stop multiplier must be positive")

    def levels(self, *, side: Side, entry_price: Decimal, atr: Decimal) -> ExitLevels:
        _validate_price(entry_price)
        if atr <= 0:
            raise ValueError("ATR must be positive")
        distance = atr * self.multiplier
        stop = entry_price - distance if side == "long" else entry_price + distance
        return ExitLevels(stop_price=stop)


@dataclass(frozen=True)
class TakeProfit:
    distance: Decimal

    def __post_init__(self) -> None:
        if self.distance <= 0:
            raise ValueError("take-profit distance must be positive")

    def levels(self, *, side: Side, entry_price: Decimal) -> ExitLevels:
        _validate_price(entry_price)
        target = entry_price + self.distance if side == "long" else entry_price - self.distance
        return ExitLevels(take_profit_price=target)


@dataclass(frozen=True)
class TrailingStop:
    distance: Decimal

    def __post_init__(self) -> None:
        if self.distance <= 0:
            raise ValueError("trailing stop distance must be positive")

    def stop_price(
        self,
        *,
        side: Side,
        current_price: Decimal,
        previous_stop: Decimal | None = None,
    ) -> Decimal:
        _validate_price(current_price)
        candidate = (
            current_price - self.distance if side == "long" else current_price + self.distance
        )
        if previous_stop is None:
            return candidate
        return max(previous_stop, candidate) if side == "long" else min(previous_stop, candidate)


@dataclass(frozen=True)
class TimeExit:
    max_candles: int

    def __post_init__(self) -> None:
        if self.max_candles < 1:
            raise ValueError("max_candles must be at least 1")

    def should_exit(self, *, entry_index: int, current_index: int) -> bool:
        if entry_index < 0 or current_index < entry_index:
            raise ValueError("candle indexes must be ordered and non-negative")
        return current_index - entry_index >= self.max_candles


def _validate_price(price: Decimal) -> None:
    if price <= 0:
        raise ValueError("price must be positive")
