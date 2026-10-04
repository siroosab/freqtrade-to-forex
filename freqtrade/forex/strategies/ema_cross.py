"""Higher-timeframe-trend strategy with lower-timeframe EMA pullback entries."""

from copy import deepcopy

import pandas as pd
from pandas import DataFrame, to_numeric

from freqtrade.forex.features import ForexFeaturePipeline
from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy, informative


class ForexEmaStrategy(IStrategy):
    """Trade lower-timeframe pullbacks only in the higher-timeframe trend."""

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "5m"
    startup_candle_count = 85
    precompute_backtest_indicators = True
    live_informative_timeframes = ("4h",)
    minimal_roi = {"0": 10.0}
    stoploss = -0.10
    process_only_new_candles = True

    fast_period_opt = IntParameter(2, 18, default=12, space="buy")
    slow_period_opt = IntParameter(20, 60, default=26, space="buy")
    informative_fast_period_opt = IntParameter(8, 24, default=20, space="buy")
    informative_slow_period_opt = IntParameter(30, 80, default=50, space="buy")
    informative_adx_min_opt = IntParameter(0, 35, default=0, space="buy")
    entry_rsi_opt = DecimalParameter(50, 60, default=52, decimals=0, space="buy")
    freqai_entry_threshold = DecimalParameter(
        0.00001, 0.0005, default=0.0001, decimals=5, space="buy"
    )
    freqai_hyperopt_parameters = ("freqai_entry_threshold",)

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.fast_period_opt = deepcopy(type(self).fast_period_opt)
        self.slow_period_opt = deepcopy(type(self).slow_period_opt)
        self.informative_fast_period_opt = deepcopy(type(self).informative_fast_period_opt)
        self.informative_slow_period_opt = deepcopy(type(self).informative_slow_period_opt)
        self.informative_adx_min_opt = deepcopy(type(self).informative_adx_min_opt)
        self.entry_rsi_opt = deepcopy(type(self).entry_rsi_opt)
        self.freqai_entry_threshold = deepcopy(type(self).freqai_entry_threshold)
        if "forex_fast_period" in config:
            self.fast_period_opt.value = int(config["forex_fast_period"])
        if "forex_slow_period" in config:
            self.slow_period_opt.value = int(config["forex_slow_period"])
        if self.fast_period < 2 or self.slow_period <= self.fast_period:
            raise ValueError("EMA periods must satisfy 2 <= fast_period < slow_period")

    @property
    def informative_fast_period(self) -> int:
        return int(self.informative_fast_period_opt.value)

    @property
    def informative_slow_period(self) -> int:
        return int(self.informative_slow_period_opt.value)

    @property
    def informative_adx_min(self) -> int:
        return int(self.informative_adx_min_opt.value)

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
            (
                to_numeric(dataframe["high"], errors="coerce")
                - to_numeric(dataframe["low"], errors="coerce")
            )
            .rolling(period, min_periods=1)
            .mean()
            / close.replace(0, float("nan"))
        ).fillna(0.0)
        fast_ema = close.ewm(span=period, adjust=False).mean()
        slow_ema = close.ewm(span=period * 2, adjust=False).mean()
        dataframe[f"%-ema-spread-{period}"] = (
            (fast_ema - slow_ema) / close.replace(0, float("nan"))
        ).fillna(0.0)
        return dataframe

    def feature_engineering_standard(
        self, dataframe: DataFrame, metadata: dict, **kwargs
    ) -> DataFrame:
        dataframe["%-session-hour"] = dataframe["date"].dt.hour / 23.0
        return dataframe

    def set_freqai_targets(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        label_period = int(self.freqai_info["feature_parameters"]["label_period_candles"])
        dataframe["&-s_close"] = dataframe["close"].shift(-label_period) / dataframe["close"] - 1.0
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pipeline = ForexFeaturePipeline(
            (
                lambda frame: frame.assign(
                    fast_ema=frame["close"].ewm(span=self.fast_period, adjust=False).mean(),
                    slow_ema=frame["close"].ewm(span=self.slow_period, adjust=False).mean(),
                    rsi=self._calculate_rsi(frame["close"]),
                ),
            ),
            warmup_candles=self.startup_candle_count,
        )
        result = pipeline.apply(dataframe)
        result["htf_bias"] = self._closed_higher_timeframe_bias(result)
        if self.config.get("freqai", {}).get("enabled", False):
            result = self.freqai.start(result, metadata, self)
        return result

    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        close = to_numeric(dataframe["close"], errors="coerce")
        dataframe["ema_fast"] = close.ewm(span=self.informative_fast_period, adjust=False).mean()
        dataframe["ema_slow"] = close.ewm(span=self.informative_slow_period, adjust=False).mean()
        dataframe["ema_fast_previous"] = dataframe["ema_fast"].shift(1)
        dataframe["adx"] = self._calculate_adx(dataframe, period=14)
        return dataframe

    @staticmethod
    def _calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
        numeric_close = to_numeric(close, errors="coerce")
        change = numeric_close.diff()
        average_gain = (
            change.clip(lower=0).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        )
        average_loss = (
            -change.clip(upper=0).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        )
        relative_strength = average_gain / average_loss.replace(0, float("nan"))
        rsi = 100.0 - (100.0 / (1.0 + relative_strength))
        rsi = rsi.mask((average_loss == 0) & (average_gain > 0), 100.0)
        return rsi.mask((average_gain == 0) & (average_loss == 0), 50.0)

    @staticmethod
    def _calculate_adx(dataframe: DataFrame, period: int = 14) -> pd.Series:
        high = to_numeric(dataframe["high"], errors="coerce")
        low = to_numeric(dataframe["low"], errors="coerce")
        close = to_numeric(dataframe["close"], errors="coerce")
        upward_move = high.diff()
        downward_move = -low.diff()
        positive_dm = upward_move.where(
            (upward_move > downward_move) & (upward_move > 0), 0.0
        )
        negative_dm = downward_move.where(
            (downward_move > upward_move) & (downward_move > 0), 0.0
        )
        true_range = pd.concat(
            (
                high - low,
                (high - close.shift(1)).abs(),
                (low - close.shift(1)).abs(),
            ),
            axis=1,
        ).max(axis=1)
        smoothed_range = true_range.ewm(
            alpha=1.0 / period, min_periods=period, adjust=False
        ).mean()
        positive_di = (
            100.0
            * positive_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
            / smoothed_range.replace(0, float("nan"))
        )
        negative_di = (
            100.0
            * negative_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
            / smoothed_range.replace(0, float("nan"))
        )
        directional_sum = (positive_di + negative_di).replace(0, float("nan"))
        directional_index = (
            100.0 * (positive_di - negative_di).abs() / directional_sum
        )
        return directional_index.ewm(
            alpha=1.0 / period, min_periods=period, adjust=False
        ).mean().fillna(0.0)

    def _closed_higher_timeframe_bias(self, dataframe: DataFrame) -> pd.Series:
        required_columns = (
            "close_4h",
            "ema_fast_4h",
            "ema_slow_4h",
            "ema_fast_previous_4h",
            "adx_4h",
        )
        if not all(column in dataframe for column in required_columns):
            return pd.Series(0, index=dataframe.index, dtype="int8")

        close = to_numeric(dataframe["close_4h"], errors="coerce")
        fast = to_numeric(dataframe["ema_fast_4h"], errors="coerce")
        slow = to_numeric(dataframe["ema_slow_4h"], errors="coerce")
        previous_fast = to_numeric(dataframe["ema_fast_previous_4h"], errors="coerce")
        adx = to_numeric(dataframe["adx_4h"], errors="coerce")
        strong_trend = adx >= self.informative_adx_min
        rising = (
            (close > fast) & (fast > slow) & (fast > previous_fast) & strong_trend
        )
        falling = (
            (close < fast) & (fast < slow) & (fast < previous_fast) & strong_trend
        )
        return (rising.astype("int8") - falling.astype("int8")).rename("htf_bias")

    def populate_live_informative_indicators(
        self,
        dataframe: DataFrame,
        metadata: dict,
        live_informative_candles: dict[str, DataFrame],
    ) -> DataFrame:
        forming_candle = live_informative_candles.get("4h")
        if forming_candle is None or forming_candle.empty:
            return dataframe
        required_columns = (
            "ema_fast_4h",
            "ema_slow_4h",
            "ema_fast_previous_4h",
            "adx_4h",
        )
        if any(
            column not in dataframe or pd.isna(dataframe[column].iloc[-1])
            for column in required_columns
        ):
            raise ValueError("4h EMA warmup data is missing for the live candle")

        last_close = to_numeric(forming_candle["close"], errors="coerce").iloc[-1]
        if pd.isna(last_close):
            raise ValueError("The forming 4h candle has no numeric close")

        last_index = dataframe.index[-1]
        closed_fast = float(dataframe["ema_fast_4h"].iloc[-1])
        closed_slow = float(dataframe["ema_slow_4h"].iloc[-1])
        previous_fast = float(dataframe["ema_fast_previous_4h"].iloc[-1])
        adx = float(dataframe["adx_4h"].iloc[-1])
        fast_alpha = 2.0 / (self.informative_fast_period + 1.0)
        slow_alpha = 2.0 / (self.informative_slow_period + 1.0)
        live_fast = fast_alpha * float(last_close) + (1.0 - fast_alpha) * closed_fast
        live_slow = slow_alpha * float(last_close) + (1.0 - slow_alpha) * closed_slow
        dataframe.loc[last_index, "ema_fast_4h"] = live_fast
        dataframe.loc[last_index, "ema_slow_4h"] = live_slow
        if (
            adx >= self.informative_adx_min
            and last_close > live_fast > live_slow
            and live_fast > previous_fast
        ):
            dataframe.loc[last_index, "htf_bias"] = 1
        elif (
            adx >= self.informative_adx_min
            and last_close < live_fast < live_slow
            and live_fast < previous_fast
        ):
            dataframe.loc[last_index, "htf_bias"] = -1
        else:
            dataframe.loc[last_index, "htf_bias"] = 0
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        bias = to_numeric(
            dataframe.get("htf_bias", pd.Series(0, index=dataframe.index)),
            errors="coerce",
        ).fillna(0)
        ready = ForexFeaturePipeline((), warmup_candles=self.startup_candle_count).ready_mask(
            dataframe
        )
        threshold = float(self.entry_rsi_opt.value)
        long_setup = (
            (dataframe["close"] > dataframe["fast_ema"])
            & (dataframe["close"].shift(1) <= dataframe["fast_ema"].shift(1))
            & (dataframe["fast_ema"] > dataframe["slow_ema"])
            & (dataframe["rsi"] >= threshold)
            & ready
        )
        short_setup = (
            (dataframe["close"] < dataframe["fast_ema"])
            & (dataframe["close"].shift(1) >= dataframe["fast_ema"].shift(1))
            & (dataframe["fast_ema"] < dataframe["slow_ema"])
            & (dataframe["rsi"] <= 100.0 - threshold)
            & ready
        )
        if "&-s_close" in dataframe:
            trusted = dataframe.get("do_predict", 0) == 1
            prediction = dataframe["&-s_close"]
            if pd.api.types.is_numeric_dtype(prediction):
                prediction = to_numeric(prediction, errors="coerce")
                threshold = float(self.freqai_entry_threshold.value)
                dataframe["enter_long"] = (
                    long_setup & (bias > 0) & (prediction > threshold) & trusted
                ).fillna(False)
                dataframe["enter_short"] = (
                    short_setup & (bias < 0) & (prediction < -threshold) & trusted
                ).fillna(False)
            else:
                dataframe["enter_long"] = (
                    long_setup & (bias > 0) & (prediction == "long") & trusted
                ).fillna(False)
                dataframe["enter_short"] = (
                    short_setup & (bias < 0) & (prediction == "short") & trusted
                ).fillna(False)
            return dataframe
        dataframe["enter_long"] = (long_setup & (bias > 0)).fillna(False)
        dataframe["enter_short"] = (short_setup & (bias < 0)).fillna(False)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        bias = to_numeric(
            dataframe.get("htf_bias", pd.Series(0, index=dataframe.index)),
            errors="coerce",
        ).fillna(0)
        if "&-s_close" in dataframe:
            trusted = dataframe.get("do_predict", 0) == 1
            prediction = dataframe["&-s_close"]
            if pd.api.types.is_numeric_dtype(prediction):
                prediction = to_numeric(prediction, errors="coerce")
                dataframe["exit_long"] = (((prediction < 0) & trusted) | (bias < 0)).fillna(False)
                dataframe["exit_short"] = (((prediction > 0) & trusted) | (bias > 0)).fillna(False)
            else:
                dataframe["exit_long"] = (((prediction != "long") & trusted) | (bias < 0)).fillna(
                    False
                )
                dataframe["exit_short"] = (((prediction != "short") & trusted) | (bias > 0)).fillna(
                    False
                )
            return dataframe
        crossed_below = (dataframe["fast_ema"] < dataframe["slow_ema"]) & (
            dataframe["fast_ema"].shift(1) >= dataframe["slow_ema"].shift(1)
        )
        crossed_above = (dataframe["fast_ema"] > dataframe["slow_ema"]) & (
            dataframe["fast_ema"].shift(1) <= dataframe["slow_ema"].shift(1)
        )
        ready = ForexFeaturePipeline((), warmup_candles=self.startup_candle_count).ready_mask(
            dataframe
        )
        dataframe["exit_long"] = ((crossed_below & ready) | (bias < 0)).fillna(False)
        dataframe["exit_short"] = ((crossed_above & ready) | (bias > 0)).fillna(False)
        return dataframe
