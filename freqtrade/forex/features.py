"""Composable feature contracts for forex strategies and FreqAI-compatible frames."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

import pandas as pd


class ForexFeature(Protocol):
    def __call__(self, dataframe: pd.DataFrame) -> pd.DataFrame: ...


class ForexFeaturePipeline:
    """Apply deterministic DataFrame features without changing candle alignment."""

    def __init__(self, features: Iterable[ForexFeature], *, warmup_candles: int = 0) -> None:
        if warmup_candles < 0:
            raise ValueError("warmup_candles must be non-negative")
        self.features = tuple(features)
        self.warmup_candles = warmup_candles

    def apply(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        result = dataframe.copy()
        original_index = result.index
        original_length = len(result)
        for feature in self.features:
            result = feature(result)
            if len(result) != original_length or not result.index.equals(original_index):
                raise ValueError("feature functions must preserve candle length and index")
        return result

    def ready_mask(self, dataframe: pd.DataFrame) -> pd.Series:
        mask = pd.Series(False, index=dataframe.index, dtype=bool)
        if self.warmup_candles < len(dataframe):
            mask.iloc[self.warmup_candles :] = True
        return mask


class ForexFreqAIAdapter:
    """Create FX-first feature columns and forward-return labels for FreqAI-style training.

    The adapter intentionally computes features from the current and historical candles only,
    while labels use the return over a fixed future horizon. This keeps the feature frame
    aligned with a single candle history and prevents direct lookahead leakage.
    """

    def __init__(
        self,
        *,
        label_period: int = 2,
        close_column: str = "close",
        indicator_periods: Iterable[int] = (5, 14),
        include_shifted_candles: int = 0,
    ) -> None:
        if label_period <= 0:
            raise ValueError("label_period must be positive")
        periods = tuple(sorted({int(period) for period in indicator_periods}))
        if not periods or any(period < 2 for period in periods):
            raise ValueError("indicator_periods must contain values >= 2")
        if include_shifted_candles < 0:
            raise ValueError("include_shifted_candles must be non-negative")
        self.label_period = label_period
        self.close_column = close_column
        self.indicator_periods = periods
        self.include_shifted_candles = include_shifted_candles

    @staticmethod
    def _to_datetime_index(frame: pd.DataFrame) -> pd.DataFrame:
        result = frame.copy()
        if "date" in result.columns:
            result["date"] = pd.to_datetime(result["date"], utc=True)
        if result.index.name == "date" or "date" in result.columns:
            index = result["date"] if "date" in result.columns else result.index
            result = result.copy()
            result.index = pd.DatetimeIndex(index)
        return result

    def build_dataset(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        result = self._to_datetime_index(dataframe).copy()
        if self.close_column not in result.columns:
            raise KeyError(f"Missing close column: {self.close_column}")

        close = pd.to_numeric(result[self.close_column], errors="coerce")
        high = pd.to_numeric(result.get("high", close), errors="coerce")
        low = pd.to_numeric(result.get("low", close), errors="coerce")
        open_ = pd.to_numeric(result.get("open", close), errors="coerce")

        result["spread_points"] = (high - low).fillna(0.0)
        result["spread_pct"] = (result["spread_points"] / close.replace(0, pd.NA)).fillna(0.0)
        true_range = (
            (high - low)
            .combine((high - close.shift(1)).abs(), max)
            .combine((low - close.shift(1)).abs(), max)
        )
        for period in self.indicator_periods:
            result[f"volatility_{period}"] = close.pct_change().abs().rolling(window=period, min_periods=1).std().fillna(0.0)
            result[f"atr_{period}"] = true_range.rolling(window=period, min_periods=1).mean().fillna(0.0)
        for lag in range(1, self.include_shifted_candles + 1):
            result[f"shift_return_{lag}"] = close.pct_change(lag).fillna(0.0)
        result["session_hour"] = pd.to_datetime(result.index).hour if isinstance(result.index, pd.DatetimeIndex) else 0
        if "date" in result.columns:
            result["session_hour"] = pd.to_datetime(result["date"]).dt.hour
        result["session_hour"] = pd.to_numeric(result["session_hour"], errors="coerce").fillna(0.0)

        future_close = close.shift(-self.label_period)
        result["label"] = ((future_close / close) - 1.0).where(close.notna() & future_close.notna(), pd.NA)
        result["label"] = result["label"].astype(float)
        return result


class ForexFreqAIExecutionGate:
    """Enforce the ordered FreqAI validation flow required by the FX roadmap.

    The research and backtest stages must complete before any dry-run is admitted.
    Model metadata can also be recorded so a later dry-run is only allowed when the
    model version, feature schema hash, and training data hash are present.
    """

    _ORDER = ("research", "backtest", "dry_run")

    def __init__(self) -> None:
        self.completed: tuple[str, ...] = ()
        self.model_version: str | None = None
        self.feature_schema_hash: str | None = None
        self.training_data_hash: str | None = None

    def register(self, phase: str) -> tuple[str, ...]:
        if phase not in self._ORDER:
            raise ValueError(f"Unsupported FreqAI phase: {phase!r}. Allowed: {', '.join(self._ORDER)}")

        if phase == "research" and self.completed:
            raise ValueError(
                "FreqAI validation must start with research and then proceed to backtest before dry-run."
            )
        if phase == "backtest" and self.completed != ("research",):
            raise ValueError(
                "FreqAI research and backtest must complete in order before dry-run is allowed."
            )
        if phase == "dry_run" and self.completed != ("research", "backtest"):
            raise ValueError(
                "FreqAI research and backtest must complete before dry-run."
            )
        if phase == "dry_run" and not self._metadata_ready():
            raise ValueError(
                "FreqAI model version, feature schema hash, and training data hash must be recorded before dry-run."
            )

        self.completed += (phase,)
        return self.completed

    def set_model_metadata(
        self,
        *,
        model_version: str | None = None,
        feature_schema_hash: str | None = None,
        training_data_hash: str | None = None,
    ) -> None:
        if model_version is not None:
            self.model_version = model_version
        if feature_schema_hash is not None:
            self.feature_schema_hash = feature_schema_hash
        if training_data_hash is not None:
            self.training_data_hash = training_data_hash

    def _metadata_ready(self) -> bool:
        return all(
            value is not None
            for value in (self.model_version, self.feature_schema_hash, self.training_data_hash)
        )
