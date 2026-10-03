"""Configurable EMA crossover strategy for FX candles."""

from copy import deepcopy

import pandas as pd
from pandas import DataFrame, to_numeric

from freqtrade.forex.features import ForexFeaturePipeline
from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy, informative
import logging

logger = logging.getLogger(__name__)

class ForexEmaStrategy(IStrategy):
    """Native Freqtrade strategy contract for a two-sided FX EMA crossover."""

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "5m"
    startup_candle_count = 26
    informative_ema_period = 20
    live_informative_timeframes = ("4h",)
    minimal_roi = {"0": 10.0}
    stoploss = -0.10
    process_only_new_candles = True

    fast_period_opt = IntParameter(2, 18, default=12, space="buy")
    slow_period_opt = IntParameter(20, 60, default=26, space="buy")
    freqai_entry_threshold = DecimalParameter(
        0.00001, 0.01, default=0.0005, decimals=5, space="buy"
    )
    freqai_hyperopt_parameters = ("freqai_entry_threshold",)

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.fast_period_opt = deepcopy(type(self).fast_period_opt)
        self.slow_period_opt = deepcopy(type(self).slow_period_opt)
        if "forex_fast_period" in config:
            self.fast_period_opt.value = int(config["forex_fast_period"])
        if "forex_slow_period" in config:
            self.slow_period_opt.value = int(config["forex_slow_period"])
        if self.fast_period < 2 or self.slow_period <= self.fast_period:
            raise ValueError("EMA periods must satisfy 2 <= fast_period < slow_period")

    @property
    def fast_period(self) -> int:
        return int(self.fast_period_opt.value)

    @property
    def slow_period(self) -> int:
        return int(self.slow_period_opt.value)

    def feature_engineering_expand_all(
        self, dataframe: DataFrame, period: int, metadata: dict, **kwargs
    ) -> DataFrame:
        close = to_numeric(dataframe["close"], errors="coerce")
        dataframe[f"%-return-{period}"] = close.pct_change(period).fillna(0.0)
        dataframe[f"%-range-{period}"] = (
            (to_numeric(dataframe["high"], errors="coerce")
             - to_numeric(dataframe["low"], errors="coerce"))
            .rolling(period, min_periods=1)
            .mean()
            / close.replace(0, float("nan"))
        ).fillna(0.0)
        return dataframe

    def feature_engineering_standard(
        self, dataframe: DataFrame, metadata: dict, **kwargs
    ) -> DataFrame:
        dataframe["%-session-hour"] = dataframe["date"].dt.hour / 23.0
        return dataframe

    def set_freqai_targets(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        label_period = int(
            self.freqai_info["feature_parameters"]["label_period_candles"]
        )
        dataframe["&-s_close"] = (
            dataframe["close"].shift(-label_period) / dataframe["close"] - 1.0
        )
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        logger.info(f"Populating indicators for timeframe: {self.timeframe}")
        pipeline = ForexFeaturePipeline(
            (
                lambda frame: frame.assign(
                    fast_ema=frame["close"].ewm(span=self.fast_period, adjust=False).mean(),
                    slow_ema=frame["close"].ewm(span=self.slow_period, adjust=False).mean(),
                ),
            ),
            warmup_candles=self.startup_candle_count,
        )
        result = pipeline.apply(dataframe)
        if self.config.get("freqai", {}).get("enabled", False):
            result = self.freqai.start(result, metadata, self)
        return result

    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_4h"] = dataframe["close"].ewm(
            span=self.informative_ema_period, adjust=False
        ).mean()
        return dataframe

    def populate_live_informative_indicators(
        self,
        dataframe: DataFrame,
        metadata: dict,
        live_informative_candles: dict[str, DataFrame],
    ) -> DataFrame:
        forming_candle = live_informative_candles.get("4h")
        if forming_candle is None or forming_candle.empty:
            return dataframe
        column = "ema_4h_4h"
        if column not in dataframe or pd.isna(dataframe[column].iloc[-1]):
            raise ValueError("EMA 4h warmup data is missing for the live candle")

        last_close = to_numeric(forming_candle["close"], errors="coerce").iloc[-1]
        if pd.isna(last_close):
            raise ValueError("The forming 4h candle has no numeric close")

        previous_ema = float(dataframe[column].iloc[-1])
        alpha = 2.0 / (self.informative_ema_period + 1.0)
        live_ema = alpha * float(last_close) + (1.0 - alpha) * previous_ema
        dataframe.loc[dataframe.index[-1], column] = live_ema
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        if "&-s_close" in dataframe:
            trusted = dataframe.get("do_predict", 0) == 1
            prediction = dataframe["&-s_close"]
            if pd.api.types.is_numeric_dtype(prediction):
                prediction = to_numeric(prediction, errors="coerce")
                threshold = float(self.freqai_entry_threshold.value)
                dataframe["enter_long"] = ((prediction > threshold) & trusted).fillna(False)
                dataframe["enter_short"] = ((prediction < -threshold) & trusted).fillna(False)
            else:
                dataframe["enter_long"] = (prediction == "long") & trusted
                dataframe["enter_short"] = (prediction == "short") & trusted
            return dataframe
        crossed_above = (dataframe["fast_ema"] > dataframe["slow_ema"]) & (
            dataframe["fast_ema"].shift(1) <= dataframe["slow_ema"].shift(1)
        )
        crossed_below = (dataframe["fast_ema"] < dataframe["slow_ema"]) & (
            dataframe["fast_ema"].shift(1) >= dataframe["slow_ema"].shift(1)
        )
        ready = ForexFeaturePipeline(
            (), warmup_candles=self.startup_candle_count
        ).ready_mask(dataframe)
        higher_timeframe_ema = dataframe["ema_4h_4h"]
        dataframe["enter_long"] = (
            crossed_above & ready & (dataframe["close"] > higher_timeframe_ema)
        ).fillna(False)
        dataframe["enter_short"] = (
            crossed_below & ready & (dataframe["close"] < higher_timeframe_ema)
        ).fillna(False)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        if "&-s_close" in dataframe:
            trusted = dataframe.get("do_predict", 0) == 1
            prediction = dataframe["&-s_close"]
            if pd.api.types.is_numeric_dtype(prediction):
                prediction = to_numeric(prediction, errors="coerce")
                dataframe["exit_long"] = ((prediction < 0) & trusted).fillna(False)
                dataframe["exit_short"] = ((prediction > 0) & trusted).fillna(False)
            else:
                dataframe["exit_long"] = (prediction != "long") & trusted
                dataframe["exit_short"] = (prediction != "short") & trusted
            return dataframe
        crossed_below = (dataframe["fast_ema"] < dataframe["slow_ema"]) & (
            dataframe["fast_ema"].shift(1) >= dataframe["slow_ema"].shift(1)
        )
        crossed_above = (dataframe["fast_ema"] > dataframe["slow_ema"]) & (
            dataframe["fast_ema"].shift(1) <= dataframe["slow_ema"].shift(1)
        )
        ready = ForexFeaturePipeline(
            (), warmup_candles=self.startup_candle_count
        ).ready_mask(dataframe)
        dataframe["exit_long"] = (crossed_below & ready).fillna(False)
        dataframe["exit_short"] = (crossed_above & ready).fillna(False)
        return dataframe
