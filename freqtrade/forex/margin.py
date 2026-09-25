"""Forex margin, free-margin, margin-level, and exposure contracts."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from freqtrade.forex.models import ForexQuoteRate, OandaInstrument
from freqtrade.forex.risk import quote_to_account_rate


class MarginError(ValueError):
    """Raised when a margin or exposure constraint is invalid."""


@dataclass(frozen=True)
class MarginPosition:
    instrument: OandaInstrument
    units: int
    entry_price: Decimal
    quote_to_account_rate: Decimal


@dataclass(frozen=True)
class MarginSnapshot:
    equity: Decimal
    used_margin: Decimal
    free_margin: Decimal
    margin_level: Decimal | None
    gross_exposure: Decimal


def notional_in_account_currency(
    *,
    units: int,
    price: Decimal,
    quote_to_account_rate: Decimal,
) -> Decimal:
    if units == 0 or price <= 0 or quote_to_account_rate <= 0:
        raise MarginError("units, price, and conversion rate must be valid")
    return Decimal(abs(units)) * price * quote_to_account_rate


def required_margin(*, notional: Decimal, leverage: Decimal) -> Decimal:
    if notional < 0 or leverage <= 0:
        raise MarginError("notional must be non-negative and leverage must be positive")
    return notional / leverage


def margin_for_position(
    *,
    instrument: OandaInstrument,
    units: int,
    entry_price: Decimal,
    leverage: Decimal,
    account_currency: str | None = None,
    rates: Iterable[ForexQuoteRate] = (),
    quote_to_account_rate_override: Decimal | None = None,
) -> Decimal:
    conversion = quote_to_account_rate_override
    if conversion is None:
        if account_currency is None:
            raise MarginError("account_currency is required without an explicit conversion rate")
        conversion = quote_to_account_rate(
            instrument=instrument,
            account_currency=account_currency,
            rates=rates,
        )
    notional = notional_in_account_currency(
        units=units,
        price=entry_price,
        quote_to_account_rate=conversion,
    )
    return required_margin(notional=notional, leverage=leverage)


def margin_snapshot(
    *,
    equity: Decimal,
    positions: Iterable[MarginPosition],
    leverage: Decimal,
) -> MarginSnapshot:
    if equity <= 0 or leverage <= 0:
        raise MarginError("equity and leverage must be positive")
    notionals = tuple(
        notional_in_account_currency(
            units=position.units,
            price=position.entry_price,
            quote_to_account_rate=position.quote_to_account_rate,
        )
        for position in positions
    )
    gross_exposure = sum(notionals, Decimal("0"))
    used_margin = required_margin(notional=gross_exposure, leverage=leverage)
    free_margin = equity - used_margin
    margin_level = None if used_margin == 0 else equity / used_margin * Decimal("100")
    return MarginSnapshot(
        equity=equity,
        used_margin=used_margin,
        free_margin=free_margin,
        margin_level=margin_level,
        gross_exposure=gross_exposure,
    )


def validate_exposure(*, current_exposure: Decimal, additional_exposure: Decimal, maximum: Decimal) -> Decimal:
    if current_exposure < 0 or additional_exposure < 0 or maximum <= 0:
        raise MarginError("exposure values must be valid")
    total = current_exposure + additional_exposure
    if total > maximum:
        raise MarginError("maximum exposure exceeded")
    return total
