"""Precision helpers needed by the native Forex persistence path."""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from math import ceil, floor, isnan

ROUND_DOWN = "ROUND_DOWN"
ROUND_UP = "ROUND_UP"


def amount_to_contract_precision(
    amount: float,
    amount_precision: float | None,
    precision_mode: int | None,
    contract_size: float | None,
) -> float:
    del precision_mode
    if amount_precision is None or contract_size in (None, 0):
        return amount
    contracts = amount / contract_size
    rounded = Decimal(str(contracts)).quantize(
        Decimal(1).scaleb(-int(amount_precision)), rounding=ROUND_FLOOR
    )
    return float(rounded * Decimal(str(contract_size)))


def price_to_precision(
    price: float,
    price_precision: float | None,
    precision_mode: int | None,
    *,
    rounding_mode: str = ROUND_DOWN,
) -> float:
    del precision_mode
    if price_precision is None or isnan(price):
        return price
    digits = int(price_precision)
    scale = 10**digits
    if rounding_mode == ROUND_UP:
        return ceil(price * scale) / scale
    if rounding_mode == ROUND_DOWN:
        return floor(price * scale) / scale
    return round(price, digits)