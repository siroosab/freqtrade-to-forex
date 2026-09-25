"""Native protection bridge for FX spread, margin, session, and financing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from freqtrade.forex.costs import financing_cost
from freqtrade.forex.margin import MarginSnapshot
from freqtrade.forex.models import ForexMarketSession, OandaPrice


@dataclass(frozen=True)
class ForexProtectionLimits:
    max_spread: Decimal | None = None
    min_free_margin: Decimal | None = None
    min_margin_level: Decimal | None = None
    allowed_sessions: tuple[ForexMarketSession, ...] = ()
    max_financing_cost: Decimal | None = None


@dataclass(frozen=True)
class ForexProtectionDecision:
    allowed: bool
    reasons: tuple[str, ...] = ()


class ForexNativeProtectionBridge:
    """Evaluate native-entry protections using FX-native measurements."""

    def __init__(self, limits: ForexProtectionLimits) -> None:
        self.limits = limits

    def evaluate(
        self,
        *,
        price: OandaPrice,
        margin: MarginSnapshot,
        moment: str | datetime,
        entry_price: Decimal,
        units: int,
        days: Decimal,
        financing_rate_per_day: Decimal,
        quote_to_account_rate: Decimal = Decimal("1"),
        direction: str | None = None,
    ) -> ForexProtectionDecision:
        reasons: list[str] = []
        if self.limits.max_spread is not None and price.spread > self.limits.max_spread:
            reasons.append("spread exceeds maximum")
        if self.limits.min_free_margin is not None and margin.free_margin < self.limits.min_free_margin:
            reasons.append("free margin below minimum")
        if (
            self.limits.min_margin_level is not None
            and margin.margin_level is not None
            and margin.margin_level < self.limits.min_margin_level
        ):
            reasons.append("margin level below minimum")
        if self.limits.allowed_sessions and not any(
            session.is_open_at(moment) for session in self.limits.allowed_sessions
        ):
            reasons.append("market session is closed")
        if self.limits.max_financing_cost is not None:
            cost = financing_cost(
                entry_price=entry_price,
                units=units,
                days=days,
                rate_per_day=financing_rate_per_day,
                quote_to_account_rate=quote_to_account_rate,
                direction=direction,
            )
            if cost > self.limits.max_financing_cost:
                reasons.append("financing cost exceeds maximum")
        return ForexProtectionDecision(allowed=not reasons, reasons=tuple(reasons))
