"""Forex Master strategy adapted from the Forex Master v4.0 Pine strategy."""

from copy import deepcopy

import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy


class ForexMasterStrategy(IStrategy):
    """Trade Bollinger-band reversals when the fast ADX EMA is below the slow EMA."""

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "15m"
    startup_candle_count = 150
    precompute_backtest_indicators = True
    minimal_roi = {
        "0": 0.0005,
        "60": 0.0004,
        "180": 0.0003,
        "360": 0.0002,
        "720": 0.0,
    }
    stoploss = -0.50
    process_only_new_candles = True
    use_exit_signal = True

    band_length = IntParameter(5, 50, default=20, space="buy")
    band_multiplier = DecimalParameter(0.5, 3.0, default=1.5, decimals=1, space="buy")
    adx_length = IntParameter(5, 100, default=50, space="buy")
    adx_fast_ema_length = IntParameter(2, 20, default=6, space="buy")
    adx_slow_ema_length = IntParameter(3, 30, default=12, space="buy")
    exit_band_offset = DecimalParameter(0.0, 1.0, default=0.0, decimals=2, space="sell")

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config or {})
        for parameter_name in (
            "band_length",
            "band_multiplier",
            "adx_length",
            "adx_fast_ema_length",
            "adx_slow_ema_length",
            "exit_band_offset",
        ):
            setattr(self, parameter_name, deepcopy(getattr(type(self), parameter_name)))

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        high = pd.to_numeric(dataframe["high"], errors="coerce")
        low = pd.to_numeric(dataframe["low"], errors="coerce")
        close = pd.to_numeric(dataframe["close"], errors="coerce")
        band_length = int(self.band_length.value)

        basis = close.rolling(band_length, min_periods=band_length).mean()
        standard_deviation = close.rolling(band_length, min_periods=band_length).std(ddof=0)
        dataframe["basis"] = basis
        dataframe["std_dev"] = float(self.band_multiplier.value) * standard_deviation
        dataframe["upper"] = basis + dataframe["std_dev"]
        dataframe["lower"] = basis - dataframe["std_dev"]

        previous_close = close.shift(1).fillna(0.0)
        previous_high = high.shift(1).fillna(0.0)
        previous_low = low.shift(1).fillna(0.0)
        true_range = pd.concat(
            (
                high - low,
                (high - previous_close).abs(),
                (low - previous_close).abs(),
            ),
            axis=1,
        ).max(axis=1)
        upward_move = high - previous_high
        downward_move = previous_low - low
        positive_dm = upward_move.where((upward_move > downward_move) & (upward_move > 0.0), 0.0)
        negative_dm = downward_move.where(
            (downward_move > upward_move) & (downward_move > 0.0), 0.0
        )

        alpha = 1.0 / int(self.adx_length.value)
        smoothed_true_range = true_range.ewm(alpha=alpha, adjust=False).mean()
        smoothed_positive_dm = positive_dm.ewm(alpha=alpha, adjust=False).mean()
        smoothed_negative_dm = negative_dm.ewm(alpha=alpha, adjust=False).mean()
        safe_true_range = smoothed_true_range.replace(0.0, float("nan"))
        di_plus = 100.0 * smoothed_positive_dm / safe_true_range
        di_minus = 100.0 * smoothed_negative_dm / safe_true_range
        di_sum = (di_plus + di_minus).replace(0.0, float("nan"))
        dx = (100.0 * (di_plus - di_minus).abs() / di_sum).fillna(0.0)
        dataframe["di_plus"] = di_plus
        dataframe["di_minus"] = di_minus
        dataframe["adx_fast"] = dx.ewm(
            span=int(self.adx_fast_ema_length.value), adjust=False
        ).mean()
        dataframe["adx_slow"] = dx.ewm(
            span=int(self.adx_slow_ema_length.value), adjust=False
        ).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        close = pd.to_numeric(dataframe["close"], errors="coerce")
        previous_close = close.shift(1)
        long_signal = (
            (close > dataframe["lower"])
            & (previous_close <= dataframe["lower"].shift(1))
            & (dataframe["adx_fast"] < dataframe["adx_slow"])
        )
        short_signal = (
            (close < dataframe["upper"])
            & (previous_close >= dataframe["upper"].shift(1))
            & (dataframe["adx_fast"] < dataframe["adx_slow"])
        )

        dataframe["enter_long"] = long_signal.fillna(False)
        dataframe["enter_short"] = short_signal.fillna(False)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        close = pd.to_numeric(dataframe["close"], errors="coerce")
        previous_close = close.shift(1)
        exit_offset = float(self.exit_band_offset.value)
        upper_exit = dataframe["upper"] + (dataframe["std_dev"] * exit_offset)
        lower_exit = dataframe["lower"] - (dataframe["std_dev"] * exit_offset)

        dataframe["exit_long"] = (
            (close >= upper_exit)
            & (previous_close < upper_exit.shift(1))
            & (dataframe["adx_fast"] >= dataframe["adx_slow"])
        ).fillna(False)
        dataframe["exit_short"] = (
            (close <= lower_exit)
            & (previous_close > lower_exit.shift(1))
            & (dataframe["adx_fast"] >= dataframe["adx_slow"])
        ).fillna(False)
        return dataframe
