# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
# © nicks1008
"""Crude oil scalp strategy translated from the CRUDE OIL BUY/SELL Pine indicator."""

import math
from copy import deepcopy

import pandas as pd
from pandas import DataFrame, Series

from freqtrade.strategy import IntParameter, IStrategy


def _wilder_average(values: Series, period: int) -> Series:
    """Calculate Wilder's RMA with the SMA seed used by Pine Script."""
    result = [float("nan")] * len(values)
    seed: list[float] = []
    average: float | None = None

    for index, value in enumerate(values.to_numpy(dtype=float)):
        if not math.isfinite(value):
            seed.clear()
            average = None
            continue
        if average is None:
            seed.append(float(value))
            if len(seed) == period:
                average = sum(seed) / period
                result[index] = average
        else:
            average += (float(value) - average) / period
            result[index] = average

    return Series(result, index=values.index, dtype=float)


class CrudeOilScalpStrategy(IStrategy):
    """Trade the indicator's BUY/SELL labels and close on its RSI reversal alerts."""

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "3m"
    startup_candle_count = 202
    process_only_new_candles = True
    use_exit_signal = True
    minimal_roi: dict[str, float] = {}
    stoploss = -0.99

    rsi_length = IntParameter(2, 100, default=14, space="buy")
    rsi_high = IntParameter(60, 95, default=80, space="sell")
    rsi_low = IntParameter(5, 40, default=20, space="sell")
    sma_length = IntParameter(5, 200, default=70, space="buy")

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config or {})
        for parameter_name in ("rsi_length", "rsi_high", "rsi_low", "sma_length"):
            setattr(self, parameter_name, deepcopy(getattr(type(self), parameter_name)))

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        close = pd.to_numeric(dataframe["close"], errors="coerce")
        sma_length = int(self.sma_length.value)
        dataframe["sma"] = close.rolling(sma_length, min_periods=sma_length).mean()

        change = close.diff()
        average_gain = _wilder_average(change.clip(lower=0.0), int(self.rsi_length.value))
        average_loss = _wilder_average((-change.clip(upper=0.0)), int(self.rsi_length.value))
        relative_strength = average_gain / average_loss
        dataframe["rsi"] = 100.0 - (100.0 / (1.0 + relative_strength))
        dataframe.loc[(average_gain == 0.0) & (average_loss == 0.0), "rsi"] = 50.0
        dataframe.loc[(average_loss == 0.0) & (average_gain > 0.0), "rsi"] = 100.0

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        open_price = pd.to_numeric(dataframe["open"], errors="coerce")
        high = pd.to_numeric(dataframe["high"], errors="coerce")
        low = pd.to_numeric(dataframe["low"], errors="coerce")
        close = pd.to_numeric(dataframe["close"], errors="coerce")
        sma = pd.to_numeric(dataframe["sma"], errors="coerce")

        prior_close = close.shift(1)
        close_crossed_above_sma = (
            (close.shift(2) <= sma.shift(1))
            & (prior_close > sma)
        )
        prior_low = low.shift(1)
        low_crossed_below_sma = (
            (low.shift(2) >= sma.shift(1))
            & (prior_low < sma)
        )

        dataframe["enter_long"] = (
            close_crossed_above_sma
            & (open_price.shift(1) < prior_close)
            & (high > high.shift(1))
            & (close > open_price)
        ).fillna(False)
        dataframe["enter_short"] = (
            low_crossed_below_sma
            & (close.shift(1) < open_price.shift(1))
            & (low < prior_low)
            & (close < open_price)
        ).fillna(False)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        rsi = pd.to_numeric(dataframe["rsi"], errors="coerce")
        open_price = pd.to_numeric(dataframe["open"], errors="coerce")
        high_level = int(self.rsi_high.value)
        low_level = int(self.rsi_low.value)

        dataframe["exit_long"] = (
            (rsi.shift(1) >= high_level) & (rsi < high_level)
        ).fillna(False)
        dataframe["exit_short"] = (
            (rsi.shift(1) <= low_level)
            & (rsi > low_level)
            & (open_price < pd.to_numeric(dataframe["close"], errors="coerce"))
        ).fillna(False)
        return dataframe
