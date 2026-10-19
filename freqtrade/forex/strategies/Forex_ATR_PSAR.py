"""Forex strategy translated from the ATR Parabolic SAR Pine Script."""

import math
from copy import deepcopy

import pandas as pd
from pandas import DataFrame, Series

from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy


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


class ForexAtrPsarStrategy(IStrategy):
    """Enter on the Nth ATR-scaled PSAR trend candle and exit on reversals."""

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "15m"
    startup_candle_count = 50
    process_only_new_candles = True
    use_exit_signal = True
    minimal_roi: dict[str, float] = {}
    stoploss = -0.99

    atr_length = IntParameter(5, 30, default=14, space="buy")
    psar_start = DecimalParameter(0.01, 0.05, default=0.02, decimals=3, space="buy")
    psar_increment = DecimalParameter(0.005, 0.05, default=0.02, decimals=3, space="buy")
    psar_maximum = DecimalParameter(0.05, 0.50, default=0.20, decimals=2, space="buy")
    entry_bars = IntParameter(1, 10, default=1, space="buy")
    hyperopt_search_bounds = {
        "atr_length": (5, 30),
        "psar_start": (0.01, 0.05),
        "psar_increment": (0.005, 0.05),
        "psar_maximum": (0.05, 0.50),
        "entry_bars": (1, 10),
    }

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config or {})
        for parameter_name in (
            "atr_length",
            "psar_start",
            "psar_increment",
            "psar_maximum",
            "entry_bars",
        ):
            setattr(self, parameter_name, deepcopy(getattr(type(self), parameter_name)))

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        high = pd.to_numeric(dataframe["high"], errors="coerce")
        low = pd.to_numeric(dataframe["low"], errors="coerce")
        close = pd.to_numeric(dataframe["close"], errors="coerce")
        previous_close = close.shift(1)
        true_range = pd.concat(
            (
                high - low,
                (high - previous_close).abs(),
                (low - previous_close).abs(),
            ),
            axis=1,
        ).max(axis=1)
        true_range = true_range.fillna(high - low)
        atr = _wilder_average(true_range, int(self.atr_length.value)).fillna(true_range)

        start = float(self.psar_start.value)
        increment = float(self.psar_increment.value)
        maximum = float(self.psar_maximum.value)
        length = len(dataframe)
        psar_values = [float("nan")] * length
        trend_directions = [0] * length
        trend_bars = [0] * length
        open_values = pd.to_numeric(dataframe["open"], errors="coerce").to_numpy(dtype=float)
        high_values = high.to_numpy(dtype=float)
        low_values = low.to_numpy(dtype=float)
        close_values = close.to_numpy(dtype=float)
        atr_values = atr.to_numpy(dtype=float)

        trend_direction = 0
        acceleration_factor = start
        extreme_point = float("nan")
        previous_sar = float("nan")
        previous_trend_bars = 0

        for index, (open_price, high_price, low_price, close_price, atr_value) in enumerate(
            zip(open_values, high_values, low_values, close_values, atr_values, strict=True)
        ):
            if not all(
                math.isfinite(value)
                for value in (open_price, high_price, low_price, close_price, atr_value)
            ):
                trend_direction = 0
                acceleration_factor = start
                extreme_point = float("nan")
                previous_sar = float("nan")
                previous_trend_bars = 0
                continue

            if trend_direction == 0:
                if index == 0:
                    continue
                previous_candle = (
                    all(
                        math.isfinite(value)
                        for value in (
                            open_values[index - 1],
                            high_values[index - 1],
                            low_values[index - 1],
                            close_values[index - 1],
                        )
                    )
                )
                if previous_candle:
                    trend_direction = (
                        1 if close_values[index - 1] > open_values[index - 1] else -1
                    )
                    previous_sar = (
                        low_values[index - 1]
                        if trend_direction == 1
                        else high_values[index - 1]
                    )
                    previous_trend_bars = trend_direction
                else:
                    trend_direction = 1 if close_price > open_price else -1
                    previous_sar = low_price if trend_direction == 1 else high_price
                acceleration_factor = start
                extreme_point = high_price if trend_direction == 1 else low_price
            else:
                long_to_short = trend_direction == 1 and close_price <= previous_sar
                short_to_long = trend_direction == -1 and close_price >= previous_sar

                if long_to_short or short_to_long:
                    trend_direction = -1 if long_to_short else 1
                    previous_trend_bars = trend_direction
                    previous_sar, extreme_point = extreme_point, (
                        high_price if trend_direction == 1 else low_price
                    )
                    acceleration_factor = start
                else:
                    previous_trend_bars += trend_direction
                    new_extreme = (
                        high_price > extreme_point
                        if trend_direction == 1
                        else low_price < extreme_point
                    )
                    if new_extreme:
                        extreme_point = high_price if trend_direction == 1 else low_price
                        acceleration_factor = min(maximum, acceleration_factor + increment)
                    previous_sar += (
                        trend_direction * acceleration_factor * atr_value
                    )

            psar_values[index] = previous_sar
            trend_directions[index] = trend_direction
            trend_bars[index] = previous_trend_bars

        dataframe["atr"] = atr
        dataframe["psar"] = psar_values
        dataframe["trend_dir"] = trend_directions
        dataframe["trend_bars"] = trend_bars
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        entry_bars = int(self.entry_bars.value)
        dataframe["enter_long"] = dataframe["trend_bars"] == entry_bars
        dataframe["enter_short"] = dataframe["trend_bars"] == -entry_bars
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        trend_direction = dataframe["trend_dir"]
        previous_direction = trend_direction.shift(1)
        dataframe["exit_long"] = ((previous_direction == 1) & (trend_direction == -1)).fillna(
            False
        )
        dataframe["exit_short"] = ((previous_direction == -1) & (trend_direction == 1)).fillna(
            False
        )
        return dataframe
