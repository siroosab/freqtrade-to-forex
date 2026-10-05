"""Composable DataFrame feature contracts for forex strategies."""

from __future__ import annotations

from collections.abc import Iterable
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
