"""Safe FX AI baseline strategy for research/backtest/dry-run only.

This strategy is intentionally limited to a read-only or dry-run operational profile.
It exposes the same Freqtrade strategy contract while using deterministic feature
engineering based on spread, volatility, ATR and session context without enabling
live execution paths.
"""

from __future__ import annotations

import pandas as pd

from freqtrade.forex.features import ForexFeaturePipeline
from freqtrade.forex.strategy_loop import Signal
from freqtrade.strategy import IStrategy


class ForexAIStrategyBaseline(IStrategy):
    """Basic FX AI baseline that stays within dry-run and Practice-safe flows."""

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "5m"
    startup_candle_count = 6
    minimal_roi = {"0": 10.0}
    stoploss = -0.12
    process_only_new_candles = True

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config or {})
        config = config or {}
        self.model = str(config.get("forex_ai_model", "hybrid")).lower()
        self.execution_mode = "dry-run"
        self.label_period = int(config.get("forex_ai_label_period", 2))
        self.entry_threshold = float(config.get("forex_ai_entry_threshold", 0.5))
        self.exit_threshold = float(config.get("forex_ai_exit_threshold", 0.0))
        self.volatility_window = int(config.get("forex_ai_volatility_window", 5))
        self.atr_window = int(config.get("forex_ai_atr_window", 14))
        self.max_spread_pct = float(config.get("forex_ai_max_spread_pct", 1.0))
        self.feature_set = {str(item).lower() for item in config.get("forex_ai_features", ["trend", "spread", "session", "volatility"])}
        if self.label_period <= 0:
            raise ValueError("forex_ai_label_period must be positive")
        if self.entry_threshold <= 0 or self.exit_threshold < 0:
            raise ValueError("AI thresholds must be non-negative and entry threshold must be positive")
        if self.volatility_window < 2 or self.atr_window < 2:
            raise ValueError("AI indicator windows must be at least 2")
        if self.max_spread_pct <= 0:
            raise ValueError("forex_ai_max_spread_pct must be positive")

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        result = dataframe.copy()
        close = pd.to_numeric(result.get("close", 0), errors="coerce").fillna(0.0)
        high = pd.to_numeric(result.get("high", close), errors="coerce").fillna(close)
        low = pd.to_numeric(result.get("low", close), errors="coerce").fillna(close)

        result["spread_points"] = (high - low).fillna(0.0)
        result["spread_pct"] = ((result["spread_points"] / close.replace(0, pd.NA)).fillna(0.0))
        result["volatility_5"] = close.pct_change().abs().rolling(window=self.volatility_window, min_periods=1).std().fillna(0.0)
        atr = (
            (high - low)
            .combine((high - close.shift(1)).abs(), max)
            .combine((low - close.shift(1)).abs(), max)
            .rolling(window=self.atr_window, min_periods=1)
            .mean()
            .fillna(0.0)
        )
        result["atr_14"] = atr

        if "date" in result.columns:
            result["session_hour"] = pd.to_datetime(result["date"], errors="coerce").dt.hour.fillna(0.0)
        else:
            result["session_hour"] = 0.0

        trend = close.pct_change().fillna(0.0).rolling(window=3, min_periods=1).mean() if "trend" in self.feature_set else 0.0
        risk_component = (result["spread_pct"] * 100.0).clip(lower=0.0) if "spread" in self.feature_set else 0.0
        result["signal_strength"] = (trend * 100.0 + risk_component).where(result["spread_pct"] <= self.max_spread_pct, 0.0).fillna(0.0)
        return result

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        result = dataframe.copy()
        ready = ForexFeaturePipeline((), warmup_candles=self.startup_candle_count).ready_mask(result)
        signal_strength = pd.to_numeric(result.get("signal_strength", 0), errors="coerce").fillna(0.0)
        result["enter_long"] = ((signal_strength > self.entry_threshold) & ready).fillna(False)
        result["enter_short"] = ((signal_strength < -self.entry_threshold) & ready).fillna(False)
        return result

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        result = dataframe.copy()
        ready = ForexFeaturePipeline((), warmup_candles=self.startup_candle_count).ready_mask(result)
        signal_strength = pd.to_numeric(result.get("signal_strength", 0), errors="coerce").fillna(0.0)
        result["exit_long"] = ((signal_strength < self.exit_threshold) & ready).fillna(False)
        result["exit_short"] = ((signal_strength > self.exit_threshold) & ready).fillna(False)
        return result

    def signal(self, candles: pd.DataFrame) -> Signal:
        """Expose the baseline through the forex backtester strategy contract."""
        indicators = self.populate_indicators(candles, {})
        entries = self.populate_entry_trend(indicators, {})
        if entries.empty:
            return Signal.FLAT
        latest = entries.iloc[-1]
        if bool(latest.get("enter_long", False)):
            return Signal.LONG
        if bool(latest.get("enter_short", False)):
            return Signal.SHORT
        return Signal.FLAT

    def signal_trace(self, candles: pd.DataFrame) -> dict[str, object]:
        """Return the latest explainable signal and feature values."""
        indicators = self.populate_indicators(candles, {})
        entries = self.populate_entry_trend(indicators, {})
        if entries.empty:
            return {"signal": Signal.FLAT.value, "reason": "no_candles"}
        latest = entries.iloc[-1]
        signal = self.signal(candles)
        reason = "flat_below_entry_threshold"
        latest_spread = float(latest.get("spread_pct", 0.0))
        if latest_spread > self.max_spread_pct:
            reason = "blocked_spread_limit"
        if latest_spread <= self.max_spread_pct and signal is Signal.LONG:
            reason = "positive_signal_strength_above_entry_threshold"
        elif latest_spread <= self.max_spread_pct and signal is Signal.SHORT:
            reason = "negative_signal_strength_below_entry_threshold"
        return {
            "time": str(latest.get("date", "")),
            "signal": signal.value,
            "reason": reason,
            "signalStrength": float(latest.get("signal_strength", 0.0)),
            "entryThreshold": self.entry_threshold,
            "spreadPct": float(latest.get("spread_pct", 0.0)),
            "volatility": float(latest.get("volatility_5", 0.0)),
            "atr": float(latest.get("atr_14", 0.0)),
            "sessionHour": float(latest.get("session_hour", 0.0)),
            "features": sorted(self.feature_set),
        }
