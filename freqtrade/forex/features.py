"""Composable DataFrame feature contracts for forex strategies."""

from __future__ import annotations

from collections.abc import Iterable
from numbers import Integral
from typing import Protocol

import pandas as pd
import talib.abstract as ta


class ForexFeature(Protocol):
    def __call__(self, dataframe: pd.DataFrame) -> pd.DataFrame: ...


def add_chained_ema_indicators(
    dataframe: pd.DataFrame,
    ema_count: int,
    timeperiod: int,
) -> pd.DataFrame:
    """Add chained EMA columns and per-candle counts above and below ``ema_x_2``."""
    if not isinstance(ema_count, Integral) or isinstance(ema_count, bool) or ema_count < 1:
        raise ValueError("ema_count must be a positive integer")
    if not isinstance(timeperiod, Integral) or isinstance(timeperiod, bool) or timeperiod < 1:
        raise ValueError("timeperiod must be a positive integer")

    ema_columns = ["ema_x_2"]
    dataframe[ema_columns[0]] = ta.EMA(dataframe["close"], timeperiod=2)

    for index in range(1, int(ema_count)):
        column = f"ema_x_{index + 2}"
        dataframe[column] = ta.EMA(
            dataframe[ema_columns[-1]],
            timeperiod=int(timeperiod),
        )
        ema_columns.append(column)

    baseline = dataframe["ema_x_2"]
    subsequent_emas = dataframe.loc[:, ema_columns[1:]]
    dataframe["count_hi"] = subsequent_emas.gt(baseline, axis=0).sum(axis=1).astype("int64")
    dataframe["count_low"] = subsequent_emas.lt(baseline, axis=0).sum(axis=1).astype("int64")
    return dataframe


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
