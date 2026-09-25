"""Explicit netting, hedging, close-by-opposite, and reduce-only semantics."""

from dataclasses import dataclass
from enum import StrEnum


class PositionMode(StrEnum):
    NETTING = "netting"
    HEDGING = "hedging"


class PositionSemanticsError(ValueError):
    """Raised when an order intent is incompatible with position semantics."""


@dataclass(frozen=True)
class PositionTransition:
    current_units: int
    submitted_units: int
    resulting_units: int
    closed_units: int
    opened_units: int


def apply_order(
    *,
    current_units: int,
    requested_units: int,
    mode: PositionMode = PositionMode.NETTING,
    reduce_only: bool = False,
) -> PositionTransition:
    if requested_units == 0:
        raise PositionSemanticsError("requested units cannot be zero")
    if current_units == 0 and reduce_only:
        raise PositionSemanticsError("reduce-only order requires an open position")
    if reduce_only and current_units * requested_units >= 0:
        raise PositionSemanticsError("reduce-only order must oppose the current position")

    submitted_units = requested_units
    if reduce_only:
        submitted_units = _opposite_capped_units(current_units, requested_units)
    resulting_units = current_units + submitted_units
    closed_units = min(abs(current_units), abs(submitted_units)) if current_units * submitted_units < 0 else 0
    opened_units = abs(submitted_units) if current_units * submitted_units >= 0 else abs(resulting_units)
    if mode is PositionMode.HEDGING and reduce_only:
        raise PositionSemanticsError("hedging reduce-only requires an explicit trade identifier")
    return PositionTransition(
        current_units=current_units,
        submitted_units=submitted_units,
        resulting_units=resulting_units,
        closed_units=closed_units,
        opened_units=opened_units,
    )


def close_by_opposite(*, current_units: int, mode: PositionMode = PositionMode.NETTING) -> PositionTransition:
    if current_units == 0:
        raise PositionSemanticsError("cannot close an empty position")
    if mode is PositionMode.HEDGING:
        raise PositionSemanticsError("hedging close-by-opposite requires explicit trade identifiers")
    return apply_order(
        current_units=current_units,
        requested_units=-current_units,
        mode=mode,
        reduce_only=True,
    )


def _opposite_capped_units(current_units: int, requested_units: int) -> int:
    magnitude = min(abs(current_units), abs(requested_units))
    return magnitude if requested_units > 0 else -magnitude
