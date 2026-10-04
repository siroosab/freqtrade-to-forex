"""Forex adaptation of the sample strategy's RSI and higher-timeframe trend entries."""

from copy import deepcopy

import pandas as pd
from pandas import DataFrame

from freqtrade.forex.features import ForexFeaturePipeline
from freqtrade.strategy import (
    CategoricalParameter,
    DecimalParameter,
    IntParameter,
    IStrategy,
    informative,
)


class ForexSampleStrategy(IStrategy):
    """Trade RSI signals and 4-hour EMA trends on a forex candle feed."""

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "15m"
    startup_candle_count = 160
    precompute_backtest_indicators = True
    minimal_roi = {
        "0": 0.0005,
        "60": 0.0004,
        "180": 0.0003,
        "360": 0.0002,
        "720": 0.0,
    }
    stoploss = -0.30
    process_only_new_candles = True
    use_exit_signal = False

    # Hyperopt tunes the RSI crossover levels and maximum trend-streak lengths.
    buy_rsi = IntParameter(12, 55, default=30, space="buy")  # Long-entry RSI threshold.
    sell_rsi = IntParameter(45, 85, default=70, space="buy")  # Short-entry RSI threshold.
    max_streak = CategoricalParameter(
        [25, 55, 100, 125, 150], default=25, space="buy"
    )  # Maximum bullish-trend streak for long entries.
    min_streak = CategoricalParameter(
        [25, 55, 100, 125, 150], default=25, space="buy"
    )  # Maximum bearish-trend streak for short entries.
    # AI prediction thresholds are tuned separately for regression and classification.
    freqai_entry_threshold = DecimalParameter(
        0.00001, 0.0005, default=0.0001, decimals=5, space="buy"
    )  # Minimum predicted return magnitude required for an entry.
    freqai_confidence_threshold = DecimalParameter(
        0.34, 0.95, default=0.5, decimals=2, space="buy"
    )  # Minimum classifier confidence required to accept a direction.
    # Hyperopt selects the regression threshold when optimizing a FreqAI regressor.
    freqai_hyperopt_parameters = ("freqai_entry_threshold",)
    # Hyperopt selects the confidence threshold when optimizing a FreqAI classifier.
    freqai_classifier_hyperopt_parameters = ("freqai_confidence_threshold",)

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config or {})
        self.buy_rsi = deepcopy(type(self).buy_rsi)
        self.sell_rsi = deepcopy(type(self).sell_rsi)
        self.max_streak = deepcopy(type(self).max_streak)
        self.min_streak = deepcopy(type(self).min_streak)
        self.freqai_entry_threshold = deepcopy(type(self).freqai_entry_threshold)
        self.freqai_confidence_threshold = deepcopy(
            type(self).freqai_confidence_threshold
        )

    def feature_engineering_expand_all(
        self, dataframe: DataFrame, period: int, metadata: dict, **kwargs
    ) -> DataFrame:
        close = pd.to_numeric(dataframe["close"], errors="coerce")
        high = pd.to_numeric(dataframe["high"], errors="coerce")
        low = pd.to_numeric(dataframe["low"], errors="coerce")
        dataframe[f"%-return-{period}"] = close.pct_change(period).fillna(0.0)
        dataframe[f"%-range-{period}"] = (
            (high - low).rolling(period, min_periods=1).mean() / close.replace(0, float("nan"))
        ).fillna(0.0)
        fast = close.ewm(span=period, adjust=False).mean()
        slow = close.ewm(span=period * 2, adjust=False).mean()
        dataframe[f"%-ema-spread-{period}"] = (
            (fast - slow) / close.replace(0, float("nan"))
        ).fillna(0.0)
        return dataframe

    def feature_engineering_standard(
        self, dataframe: DataFrame, metadata: dict, **kwargs
    ) -> DataFrame:
        dataframe["%-session-hour"] = pd.to_datetime(dataframe["date"], utc=True).dt.hour / 23.0
        return dataframe

    def set_freqai_targets(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        label_period = int(self.freqai_info["feature_parameters"]["label_period_candles"])
        close = pd.to_numeric(dataframe["close"], errors="coerce")
        dataframe["&-s_close"] = close.shift(-label_period) / close - 1.0
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pipeline = ForexFeaturePipeline(
            (self._populate_base_indicators,),
            warmup_candles=self.startup_candle_count,
        )
        result = pipeline.apply(dataframe)
        if self.config.get("freqai", {}).get("enabled", False):
            result = self.freqai.start(result, metadata, self)
        return result

    @staticmethod
    def _populate_base_indicators(dataframe: DataFrame) -> DataFrame:
        open_ = pd.to_numeric(dataframe["open"], errors="coerce")
        high = pd.to_numeric(dataframe["high"], errors="coerce")
        low = pd.to_numeric(dataframe["low"], errors="coerce")
        close = pd.to_numeric(dataframe["close"], errors="coerce")
        ha_close = (open_ + high + low + close) / 4.0

        change = ha_close.diff()
        average_gain = change.clip(lower=0).ewm(alpha=1.0 / 14, min_periods=14, adjust=False).mean()
        average_loss = (
            -change.clip(upper=0).ewm(alpha=1.0 / 14, min_periods=14, adjust=False).mean()
        )
        relative_strength = average_gain / average_loss.replace(0, float("nan"))
        rsi = 100.0 - (100.0 / (1.0 + relative_strength))
        rsi = rsi.mask((average_loss == 0) & (average_gain > 0), 100.0)
        rsi = rsi.mask((average_gain == 0) & (average_loss == 0), 50.0)

        dataframe["ha_close"] = ha_close
        dataframe["rsi_ha"] = rsi
        dataframe["rsi_sma_ha"] = rsi.rolling(14, min_periods=14).mean()
        dataframe["rsi_ha_sma_rounded"] = dataframe["rsi_sma_ha"].round()
        dataframe["ema50"] = close.ewm(span=30, adjust=False).mean()
        return dataframe

    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        close = pd.to_numeric(dataframe["close"], errors="coerce")
        dataframe["emaX3"] = close.ewm(span=10, adjust=False).mean()
        dataframe["emaX2"] = close.ewm(span=7, adjust=False).mean()
        dataframe["emaX1"] = close.ewm(span=5, adjust=False).mean()
        dataframe["max"] = (dataframe["emaX1"] > dataframe["emaX2"]) & (
            dataframe["emaX2"] > dataframe["emaX3"]
        )
        dataframe["min"] = (dataframe["emaX1"] < dataframe["emaX2"]) & (
            dataframe["emaX2"] < dataframe["emaX3"]
        )
        return dataframe

    @staticmethod
    def _consecutive_count(series: pd.Series) -> pd.Series:
        groups = (~series).cumsum()
        return series.groupby(groups).cumsum()

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        result = dataframe.copy()
        bullish = result["max_4h"].fillna(False).astype(bool)
        bearish = result["min_4h"].fillna(False).astype(bool)
        result["max_streak_count"] = self._consecutive_count(bullish)
        result["min_streak_count"] = self._consecutive_count(bearish)

        state_changes = pd.Series(float("nan"), index=result.index)
        state_changes.loc[bullish] = 1
        state_changes.loc[bearish] = -1
        result["max_or_min"] = state_changes.ffill().fillna(0).astype("int8")

        ready = ForexFeaturePipeline((), warmup_candles=self.startup_candle_count).ready_mask(
            result
        )
        rsi = pd.to_numeric(result["rsi_ha_sma_rounded"], errors="coerce")
        previous_rsi = rsi.shift(1)
        previous_state = result["max_or_min"].shift(1)
        valid_price = pd.to_numeric(result["close"], errors="coerce").gt(0)

        long_signals = (
            (
                (previous_rsi <= self.buy_rsi.value)
                & (rsi > self.buy_rsi.value)
                & (result["max_streak_count"] < self.max_streak.value)
                & (result["max_or_min"] == 1)
            )
            | (
                (previous_state == 0)
                & (result["max_or_min"] == 1)
                & (result["close"] > result["ema50"])
            )
            | (result["min_4h"].shift(1).fillna(False) & bullish)
        )
        short_signals = (
            (
                (previous_rsi >= self.sell_rsi.value)
                & (rsi < self.sell_rsi.value)
                & (result["min_streak_count"] < self.min_streak.value)
                & (result["max_or_min"] == -1)
            )
            | (
                (previous_state == 0)
                & (result["max_or_min"] == -1)
                & (result["close"] < result["ema50"])
            )
            | (result["max_4h"].shift(1).fillna(False) & bearish)
        )

        long_signals &= ready & valid_price
        short_signals &= ready & valid_price
        if "&-s_close" in result:
            trusted = result.get("do_predict", 0) == 1
            prediction = result["&-s_close"]
            if pd.api.types.is_numeric_dtype(prediction):
                prediction = pd.to_numeric(prediction, errors="coerce")
                threshold = float(self.freqai_entry_threshold.value)
                long_signals &= prediction > threshold
                short_signals &= prediction < -threshold
            else:
                long_signals &= prediction == "long"
                short_signals &= prediction == "short"
                confidence = pd.to_numeric(
                    result.get(
                        "freqai_confidence",
                        pd.Series(1.0, index=result.index),
                    ),
                    errors="coerce",
                )
                trusted &= confidence >= float(self.freqai_confidence_threshold.value)
            long_signals &= trusted
            short_signals &= trusted

        result["enter_long"] = long_signals.fillna(False)
        result["enter_short"] = short_signals.fillna(False)
        return result

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        bullish = dataframe["max_4h"].fillna(False).astype(bool)
        bearish = dataframe["min_4h"].fillna(False).astype(bool)
        dataframe["exit_long"] = (
            dataframe["max_or_min"].shift(1).fillna(0).gt(0)
            & (dataframe["max_or_min"] < 0)
            & (bullish | bearish)
        )
        dataframe["exit_short"] = (
            dataframe["max_or_min"].shift(1).fillna(0).lt(0)
            & (dataframe["max_or_min"] > 0)
            & (bullish | bearish)
        )
        return dataframe
