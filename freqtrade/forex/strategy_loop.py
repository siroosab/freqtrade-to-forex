"""Small strategy-to-paper execution loop for the first forex slice."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

import pandas as pd

from freqtrade.forex.models import OandaInstrument
from freqtrade.forex.paper import DryRunSession, PaperPosition
from freqtrade.forex.provider import OandaMarketDataProvider
from freqtrade.forex.risk import units_for_fixed_risk


class Signal(StrEnum):
    FLAT = "flat"
    LONG = "long"
    SHORT = "short"


class CandleStrategy(Protocol):
    def signal(self, candles: pd.DataFrame) -> Signal: ...


class ForexStrategyAdapter:
    """Small Freqtrade-style strategy adapter that works with FX DataFrames."""

    def __init__(
        self,
        *,
        timeframe: str,
        indicators,
        entry_signal,
        exit_signal,
    ) -> None:
        self.timeframe = timeframe
        self._indicators = indicators
        self._entry_signal = entry_signal
        self._exit_signal = exit_signal

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        result = self._indicators(dataframe)
        if result is None:
            return dataframe
        return result

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        signal = self._entry_signal(dataframe)
        result = dataframe.copy()
        result["enter_long"] = signal.astype(bool)
        result["enter_short"] = False
        return result

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        signal = self._exit_signal(dataframe)
        result = dataframe.copy()
        result["exit_long"] = signal.astype(bool)
        result["exit_short"] = False
        return result


@dataclass(frozen=True)
class EmaCrossStrategy:
    fast_period: int = 12
    slow_period: int = 26

    def __post_init__(self) -> None:
        if self.fast_period < 2 or self.slow_period <= self.fast_period:
            raise ValueError("EMA periods must satisfy 2 <= fast_period < slow_period")

    def signal(self, candles: pd.DataFrame) -> Signal:
        if len(candles) < self.slow_period + 1:
            return Signal.FLAT
        close = candles["close"]
        fast = close.ewm(span=self.fast_period, adjust=False).mean()
        slow = close.ewm(span=self.slow_period, adjust=False).mean()
        if fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]:
            return Signal.LONG
        if fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1]:
            return Signal.SHORT
        return Signal.FLAT


@dataclass(frozen=True)
class StrategyStepResult:
    signal: Signal
    position: PaperPosition | None
    closed_position: PaperPosition | None = None
    reason: str | None = None
    order_event: str | None = None
    costs: dict[str, str | int | float] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "signal": self.signal.value,
            "position": self._position_payload(self.position),
            "closed_position": self._position_payload(self.closed_position),
            "reason": self.reason,
            "order_event": self.order_event,
            "costs": self.costs,
        }

    @staticmethod
    def _position_payload(position: PaperPosition | None) -> dict[str, object] | None:
        if position is None:
            return None
        return {
            "instrument": position.instrument,
            "units": position.units,
            "entry_price": str(position.entry_price),
            "last_bid": str(position.last_bid),
            "last_ask": str(position.last_ask),
            "trade_id": position.trade_id,
            "stop_loss_price": str(position.stop_loss_price) if position.stop_loss_price is not None else None,
            "take_profit_price": str(position.take_profit_price) if position.take_profit_price is not None else None,
            "unrealized_pl": str(position.unrealized_pl),
        }


class DryRunStrategyLoop:
    def __init__(
        self,
        provider: OandaMarketDataProvider,
        session: DryRunSession,
        strategy: CandleStrategy,
        instrument: OandaInstrument,
        *,
        account_equity: Decimal,
        risk_fraction: Decimal,
        stop_pips: Decimal,
        quote_to_account_rate: Decimal = Decimal("1"),
    ) -> None:
        if stop_pips <= 0:
            raise ValueError("stop_pips must be positive")
        self.provider = provider
        self.session = session
        self.strategy = strategy
        self.instrument = instrument
        self.account_equity = account_equity
        self.risk_fraction = risk_fraction
        self.stop_pips = stop_pips
        if quote_to_account_rate <= 0:
            raise ValueError("quote_to_account_rate must be positive")
        self.quote_to_account_rate = quote_to_account_rate

    async def step(self, pair: str, timeframe: str, *, candle_count: int = 200) -> StrategyStepResult:
        candles = await self.provider.fetch_ohlcv(pair, timeframe, count=candle_count)
        await self.session.refresh_prices()
        signal = self.strategy.signal(candles)
        instrument = self.instrument.name
        current = self.session.positions.get(instrument)
        closed: PaperPosition | None = None
        reason: str | None = None
        order_event: str | None = None
        costs: dict[str, str | int | float] | None = None

        if current and self._is_opposite(current, signal):
            closed = await self.session.close_market(instrument, f"paper-exit-{candles.iloc[-1]['date']}")
            current = None
            reason = "opposite_signal_exit"
            order_event = "filled"

        if current is None and signal is not Signal.FLAT:
            price = self.session.prices[instrument]
            entry = price.ask if signal is Signal.LONG else price.bid
            stop_distance = self.stop_pips * self.instrument.pip_size
            stop = entry - stop_distance if signal is Signal.LONG else entry + stop_distance
            units = units_for_fixed_risk(
                account_equity=self.account_equity,
                risk_fraction=self.risk_fraction,
                entry_price=entry,
                stop_price=stop,
                instrument=self.instrument,
                quote_to_account_rate=self.quote_to_account_rate,
            )
            if signal is Signal.SHORT:
                units = -units
            if units:
                current = await self.session.open_market(
                    instrument,
                    units,
                    f"paper-entry-{candles.iloc[-1]['date']}",
                    stop_loss_price=str(stop),
                )
                reason = "entry_signal"
                order_event = "filled"
                costs = {
                    "spread": str(abs(price.ask - price.bid)),
                    "estimated_cost": "0",
                }

        if signal is Signal.FLAT:
            reason = "flat_signal"
            order_event = "none"

        return StrategyStepResult(
            signal=signal,
            position=current,
            closed_position=closed,
            reason=reason,
            order_event=order_event,
            costs=costs,
        )

    @staticmethod
    def _is_opposite(position: PaperPosition, signal: Signal) -> bool:
        return (position.units > 0 and signal is Signal.SHORT) or (
            position.units < 0 and signal is Signal.LONG
        )
