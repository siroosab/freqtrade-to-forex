"""Exact decimal arithmetic used by the native Forex path."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal, localcontext
from typing import Any


class FtPrecise:
    def __init__(self, number: Any, decimals: int | None = None):
        del decimals
        self._value = Decimal(str(number))

    @classmethod
    def _from_decimal(cls, value: Decimal) -> "FtPrecise":
        result = cls.__new__(cls)
        result._value = value
        return result

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        return value._value if isinstance(value, FtPrecise) else Decimal(str(value))

    def __str__(self) -> str:
        if self._value == 0:
            return "0"
        text = format(self._value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return "0" if text in {"", "-0"} else text

    def __repr__(self) -> str:
        return f"FtPrecise('{self}')"

    def __float__(self) -> float:
        return float(self._value)

    def __ceil__(self) -> int:
        return int(self._value.to_integral_value(rounding="ROUND_CEILING"))

    def __abs__(self) -> "FtPrecise":
        return self._from_decimal(abs(self._value))

    def __neg__(self) -> "FtPrecise":
        return self._from_decimal(-self._value)

    def _operation(self, other: Any, operation: str) -> "FtPrecise":
        with localcontext() as context:
            context.prec = max(100, len(self._value.as_tuple().digits) + 50)
            value = getattr(self._value, operation)(self._decimal(other))
        return self._from_decimal(value)

    def __add__(self, other: Any) -> "FtPrecise":
        return self._operation(other, "__add__")

    def __radd__(self, other: Any) -> "FtPrecise":
        return self._from_decimal(self._decimal(other)).__add__(self)

    def __sub__(self, other: Any) -> "FtPrecise":
        return self._operation(other, "__sub__")

    def __rsub__(self, other: Any) -> "FtPrecise":
        return self._from_decimal(self._decimal(other)).__sub__(self)

    def __mul__(self, other: Any) -> "FtPrecise":
        return self._operation(other, "__mul__")

    def __rmul__(self, other: Any) -> "FtPrecise":
        return self._from_decimal(self._decimal(other)).__mul__(self)

    def __truediv__(self, other: Any) -> "FtPrecise":
        with localcontext() as context:
            context.prec = max(100, len(self._value.as_tuple().digits) + 50)
            value = self._value / self._decimal(other)
            value = value.quantize(Decimal("1e-18"), rounding=ROUND_DOWN)
        return self._from_decimal(value)

    def __rtruediv__(self, other: Any) -> "FtPrecise":
        return self._from_decimal(self._decimal(other)).__truediv__(self)

    def __mod__(self, other: Any) -> "FtPrecise":
        return self._operation(other, "__mod__")

    def __eq__(self, other: object) -> bool:
        try:
            return self._value == self._decimal(other)
        except (TypeError, ValueError, ArithmeticError):
            return False

    def __lt__(self, other: Any) -> bool:
        return self._value < self._decimal(other)

    def __le__(self, other: Any) -> bool:
        return self._value <= self._decimal(other)

    def __gt__(self, other: Any) -> bool:
        return self._value > self._decimal(other)

    def __ge__(self, other: Any) -> bool:
        return self._value >= self._decimal(other)

    @staticmethod
    def string_gt(left: str, right: str) -> bool:
        return Decimal(left) > Decimal(right)
