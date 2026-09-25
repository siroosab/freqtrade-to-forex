"""Forex position sizing utilities."""

from collections import deque
from decimal import Decimal, ROUND_DOWN
from typing import Iterable

from freqtrade.forex.models import ForexQuoteRate, OandaInstrument


class RiskSizingError(ValueError):
    """Raised when a risk-based order cannot be sized safely."""


class ForexRateBook:
    """Resolve currency conversion using direct, inverse, or chained FX rates."""

    def __init__(self, rates: Iterable[ForexQuoteRate] = ()) -> None:
        self._rates = tuple(rates)

    def convert(self, amount: Decimal, from_currency: str, to_currency: str) -> Decimal:
        source = from_currency.upper()
        target = to_currency.upper()
        if source == target:
            return amount
        queue: deque[tuple[str, Decimal]] = deque([(source, amount)])
        visited = {source}
        while queue:
            currency, converted = queue.popleft()
            for rate in self._rates:
                neighbors = self._neighbors(rate, currency)
                for next_currency, factor in neighbors:
                    if next_currency == target:
                        return converted * factor
                    if next_currency not in visited:
                        visited.add(next_currency)
                        queue.append((next_currency, converted * factor))
        raise RiskSizingError(f"missing currency conversion {source}->{target}")

    @staticmethod
    def _neighbors(rate: ForexQuoteRate, currency: str) -> tuple[tuple[str, Decimal], ...]:
        if currency == rate.base_currency:
            return ((rate.quote_currency, rate.rate),)
        if currency == rate.quote_currency:
            return ((rate.base_currency, Decimal("1") / rate.rate),)
        return ()


def quote_to_account_rate(
    *,
    instrument: OandaInstrument,
    account_currency: str,
    rates: Iterable[ForexQuoteRate] = (),
) -> Decimal:
    if instrument.quote_currency is None:
        raise RiskSizingError("instrument quote currency is required")
    return ForexRateBook(rates).convert(
        Decimal("1"), instrument.quote_currency, account_currency
    )


def pip_value_per_unit(
    *,
    instrument: OandaInstrument,
    account_currency: str,
    rates: Iterable[ForexQuoteRate] = (),
) -> Decimal:
    """Return one pip's account-currency value for one base unit."""
    return instrument.pip_size * quote_to_account_rate(
        instrument=instrument,
        account_currency=account_currency,
        rates=rates,
    )


def units_for_fixed_risk(
    *,
    account_equity: Decimal,
    risk_fraction: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    instrument: OandaInstrument,
    quote_to_account_rate: Decimal = Decimal("1"),
    account_currency: str | None = None,
    conversion_rates: Iterable[ForexQuoteRate] = (),
) -> int:
    """Return whole signed units for a long/short trade based on stop risk.

    The quote-to-account rate converts quote-currency loss into account currency.
    """
    if account_equity <= 0:
        raise RiskSizingError("account equity must be positive")
    if not Decimal("0") < risk_fraction < Decimal("1"):
        raise RiskSizingError("risk fraction must be between 0 and 1")
    if entry_price <= 0 or stop_price <= 0:
        raise RiskSizingError("prices must be positive")
    if account_currency is not None:
        quote_to_account_rate = quote_to_account_rate_for_instrument(
            instrument=instrument,
            account_currency=account_currency,
            rates=conversion_rates,
        )
    if quote_to_account_rate <= 0:
        raise RiskSizingError("quote-to-account rate must be positive")

    stop_distance = abs(entry_price - stop_price)
    risk_per_unit = stop_distance * quote_to_account_rate
    if risk_per_unit <= 0:
        raise RiskSizingError("entry and stop prices must differ")

    raw_units = account_equity * risk_fraction / risk_per_unit
    precision = Decimal(1).scaleb(-instrument.trade_units_precision)
    sized_units = raw_units.quantize(precision, rounding=ROUND_DOWN)
    minimum = instrument.minimum_trade_size
    if sized_units < minimum:
        return 0
    return int(sized_units)


def quote_to_account_rate_for_instrument(
    *,
    instrument: OandaInstrument,
    account_currency: str,
    rates: Iterable[ForexQuoteRate] = (),
) -> Decimal:
    return quote_to_account_rate(
        instrument=instrument,
        account_currency=account_currency,
        rates=rates,
    )
