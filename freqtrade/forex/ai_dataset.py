"""Reproducible dataset and split manifests for Forex FreqAI research."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd

from freqtrade.forex.features import ForexFreqAIAdapter


@dataclass(frozen=True)
class ForexAIDatasetManifest:
    pair: str
    timeframe: str
    history_mode: str
    history_value: int
    label_period: int
    feature_columns: tuple[str, ...]
    feature_schema_hash: str
    training_data_hash: str
    train_range: tuple[str, str]
    validation_range: tuple[str, str]
    out_of_sample_range: tuple[str, str]
    train_rows: int
    validation_rows: int
    out_of_sample_rows: int
    created_at: str
    indicator_periods: tuple[int, ...] = (5, 14)
    include_shifted_candles: int = 0
    train_period_days: int | None = None
    backtest_period_days: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Approximate candle counts per calendar day, used to translate FreqAI-style
# train_period_days/backtest_period_days windows into candle-based splits.
_CANDLES_PER_DAY = {"M5": 288, "M15": 96, "H1": 24}


def _range(frame: pd.DataFrame) -> tuple[str, str]:
    if frame.empty:
        return ("", "")
    dates = pd.Series(pd.to_datetime(frame["date"], utc=True) if "date" in frame else pd.to_datetime(frame.index, utc=True))
    return (dates.iloc[0].isoformat(), dates.iloc[-1].isoformat())


def build_forex_ai_dataset(
    candles: pd.DataFrame,
    *,
    pair: str,
    timeframe: str,
    history_mode: str = "candles",
    history_value: int | None = None,
    label_period: int = 2,
    indicator_periods: tuple[int, ...] = (5, 14),
    include_shifted_candles: int = 0,
    train_period_days: int | None = None,
    backtest_period_days: int | None = None,
    train_fraction: Decimal = Decimal("0.6"),
    validation_fraction: Decimal = Decimal("0.2"),
) -> tuple[pd.DataFrame, ForexAIDatasetManifest]:
    """Build an aligned, hashed dataset with chronological train/validation/OOS splits."""
    if candles.empty:
        raise ValueError("AI dataset requires candles")
    if not Decimal("0") < train_fraction < Decimal("1"):
        raise ValueError("train_fraction must be between 0 and 1")
    if not Decimal("0") < validation_fraction < Decimal("1"):
        raise ValueError("validation_fraction must be between 0 and 1")
    if train_fraction + validation_fraction >= Decimal("1"):
        raise ValueError("train and validation fractions must leave OOS data")

    dataset = ForexFreqAIAdapter(
        label_period=label_period,
        indicator_periods=indicator_periods,
        include_shifted_candles=include_shifted_candles,
    ).build_dataset(candles)
    dataset = dataset.dropna(subset=["label"]).reset_index(drop=True)
    if len(dataset) < 10:
        raise ValueError("AI dataset requires at least 10 labeled candles")

    candles_per_day = _CANDLES_PER_DAY.get(timeframe.upper())
    if train_period_days is not None and backtest_period_days is not None and candles_per_day:
        if train_period_days <= 0 or backtest_period_days <= 0:
            raise ValueError("train_period_days and backtest_period_days must be positive")
        train_end = min(len(dataset) - 2, max(1, train_period_days * candles_per_day))
        validation_end = min(len(dataset) - 1, train_end + max(1, backtest_period_days * candles_per_day))
        if validation_end <= train_end:
            validation_end = min(len(dataset) - 1, train_end + 1)
    else:
        train_end = max(1, int(len(dataset) * float(train_fraction)))
        validation_end = max(train_end + 1, int(len(dataset) * float(train_fraction + validation_fraction)))
        validation_end = min(validation_end, len(dataset) - 1)
    train = dataset.iloc[:train_end]
    validation = dataset.iloc[train_end:validation_end]
    oos = dataset.iloc[validation_end:]

    feature_columns = tuple(column for column in dataset.columns if column not in {"date", "label"})
    schema_payload = json.dumps(feature_columns, sort_keys=True).encode()
    frame_payload = dataset.to_json(orient="split", date_format="iso").encode()
    manifest = ForexAIDatasetManifest(
        pair=pair.replace("_", "/").upper(),
        timeframe=timeframe.upper(),
        history_mode=history_mode,
        history_value=history_value if history_value is not None else len(candles),
        label_period=label_period,
        feature_columns=feature_columns,
        feature_schema_hash=hashlib.sha256(schema_payload).hexdigest()[:16],
        training_data_hash=hashlib.sha256(frame_payload).hexdigest()[:16],
        train_range=_range(train),
        validation_range=_range(validation),
        out_of_sample_range=_range(oos),
        train_rows=len(train),
        validation_rows=len(validation),
        out_of_sample_rows=len(oos),
        created_at=datetime.now(timezone.utc).isoformat(),
        indicator_periods=tuple(sorted({int(period) for period in indicator_periods})),
        include_shifted_candles=include_shifted_candles,
        train_period_days=train_period_days,
        backtest_period_days=backtest_period_days,
    )
    return dataset, manifest

