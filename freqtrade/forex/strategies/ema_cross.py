"""Configurable EMA crossover strategy for FX candles."""

from pandas import DataFrame

from freqtrade.strategy import IStrategy

from freqtrade.forex.features import ForexFeaturePipeline


class ForexEmaStrategy(IStrategy):
    """Native Freqtrade strategy contract for a two-sided FX EMA crossover."""

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "5m"
    startup_candle_count = 26
    minimal_roi = {"0": 10.0}
    stoploss = -0.10
    process_only_new_candles = True

    fast_period = 12
    slow_period = 26

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.fast_period = int(config.get("forex_fast_period", type(self).fast_period))
        self.slow_period = int(config.get("forex_slow_period", type(self).slow_period))
        if self.fast_period < 2 or self.slow_period <= self.fast_period:
            raise ValueError("EMA periods must satisfy 2 <= fast_period < slow_period")

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pipeline = ForexFeaturePipeline(
            (
                lambda frame: frame.assign(
                    fast_ema=frame["close"].ewm(span=self.fast_period, adjust=False).mean(),
                    slow_ema=frame["close"].ewm(span=self.slow_period, adjust=False).mean(),
                ),
            ),
            warmup_candles=self.startup_candle_count,
        )
        return pipeline.apply(dataframe)

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        crossed_above = (dataframe["fast_ema"] > dataframe["slow_ema"]) & (
            dataframe["fast_ema"].shift(1) <= dataframe["slow_ema"].shift(1)
        )
        crossed_below = (dataframe["fast_ema"] < dataframe["slow_ema"]) & (
            dataframe["fast_ema"].shift(1) >= dataframe["slow_ema"].shift(1)
        )
        ready = ForexFeaturePipeline((), warmup_candles=self.startup_candle_count).ready_mask(dataframe)
        dataframe["enter_long"] = (crossed_above & ready).fillna(False)
        dataframe["enter_short"] = (crossed_below & ready).fillna(False)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        crossed_below = (dataframe["fast_ema"] < dataframe["slow_ema"]) & (
            dataframe["fast_ema"].shift(1) >= dataframe["slow_ema"].shift(1)
        )
        crossed_above = (dataframe["fast_ema"] > dataframe["slow_ema"]) & (
            dataframe["fast_ema"].shift(1) <= dataframe["slow_ema"].shift(1)
        )
        ready = ForexFeaturePipeline((), warmup_candles=self.startup_candle_count).ready_mask(dataframe)
        dataframe["exit_long"] = (crossed_below & ready).fillna(False)
        dataframe["exit_short"] = (crossed_above & ready).fillna(False)
        return dataframe
