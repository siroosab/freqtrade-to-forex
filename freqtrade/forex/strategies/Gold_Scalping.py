"""Gold scalping strategy translated from the Pine Script entry and risk rules."""

import math
from copy import deepcopy

import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Order, Trade
from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy, stoploss_from_absolute


class GoldScalpingStrategy(IStrategy):
    """Trade engulfing candles in the EMA trend with RSI and ATR risk controls."""

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "15m"
    startup_candle_count = 400
    process_only_new_candles = True
    use_exit_signal = True
    use_custom_stoploss = True
    minimal_roi: dict[str, float] = {}
    stoploss = -0.99

    ema_fast_period = IntParameter(20, 99, default=50, space="buy")
    ema_slow_period = IntParameter(101, 400, default=200, space="buy")
    rsi_period = IntParameter(5, 30, default=14, space="buy")
    rsi_entry_low = IntParameter(35, 49, default=45, space="buy")
    rsi_entry_high = IntParameter(51, 65, default=55, space="buy")
    atr_period = IntParameter(5, 30, default=14, space="sell")
    atr_stop_multiplier = DecimalParameter(0.5, 3.0, default=1.0, decimals=1, space="sell")
    take_profit_distance = DecimalParameter(0.5, 10.0, default=2.0, decimals=1, space="sell")
    hyperopt_search_bounds = {
        "ema_fast_period": (5, 200),
        "ema_slow_period": (30, 800),
        "rsi_period": (2, 60),
        "rsi_entry_low": (10, 49),
        "rsi_entry_high": (51, 90),
        "atr_period": (2, 100),
        "atr_stop_multiplier": (0.2, 8.0),
        "take_profit_distance": (0.2, 40.0),
    }

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config or {})
        for parameter_name in (
            "ema_fast_period",
            "ema_slow_period",
            "rsi_period",
            "rsi_entry_low",
            "rsi_entry_high",
            "atr_period",
            "atr_stop_multiplier",
            "take_profit_distance",
        ):
            setattr(self, parameter_name, deepcopy(getattr(type(self), parameter_name)))

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        close = pd.to_numeric(dataframe["close"], errors="coerce")
        high = pd.to_numeric(dataframe["high"], errors="coerce")
        low = pd.to_numeric(dataframe["low"], errors="coerce")

        fast_period = int(self.ema_fast_period.value)
        slow_period = int(self.ema_slow_period.value)
        dataframe["ema50"] = close.ewm(
            span=fast_period, adjust=False, min_periods=fast_period
        ).mean()
        dataframe["ema200"] = close.ewm(
            span=slow_period, adjust=False, min_periods=slow_period
        ).mean()

        previous_close = close.shift(1)
        true_range = pd.concat(
            (
                high - low,
                (high - previous_close).abs(),
                (low - previous_close).abs(),
            ),
            axis=1,
        ).max(axis=1)
        atr_period = int(self.atr_period.value)
        dataframe["atr"] = true_range.ewm(
            alpha=1.0 / atr_period,
            adjust=False,
            min_periods=atr_period,
        ).mean()

        change = close.diff()
        rsi_period = int(self.rsi_period.value)
        average_gain = change.clip(lower=0.0).ewm(
            alpha=1.0 / rsi_period,
            adjust=False,
            min_periods=rsi_period,
        ).mean()
        average_loss = (-change.clip(upper=0.0)).ewm(
            alpha=1.0 / rsi_period,
            adjust=False,
            min_periods=rsi_period,
        ).mean()
        relative_strength = average_gain / average_loss
        dataframe["rsi"] = 100.0 - (100.0 / (1.0 + relative_strength))
        dataframe.loc[(average_gain == 0.0) & (average_loss == 0.0), "rsi"] = 50.0
        dataframe.loc[(average_loss == 0.0) & (average_gain > 0.0), "rsi"] = 100.0

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        open_price = pd.to_numeric(dataframe["open"], errors="coerce")
        close = pd.to_numeric(dataframe["close"], errors="coerce")
        previous_open = open_price.shift(1)
        previous_close = close.shift(1)
        rsi_in_range = dataframe["rsi"].between(
            int(self.rsi_entry_low.value),
            int(self.rsi_entry_high.value),
        )

        bullish_engulfing = (
            (close > open_price)
            & (previous_close < previous_open)
            & (close > previous_close)
            & (open_price < previous_close)
        )
        bearish_engulfing = (
            (close < open_price)
            & (previous_close > previous_open)
            & (close < previous_close)
            & (open_price > previous_close)
        )

        dataframe["enter_long"] = (
            (dataframe["ema50"] > dataframe["ema200"])
            & rsi_in_range
            & bullish_engulfing
            & (close > dataframe["ema50"])
        ).fillna(False)
        dataframe["enter_short"] = (
            (dataframe["ema50"] < dataframe["ema200"])
            & rsi_in_range
            & bearish_engulfing
            & (close < dataframe["ema50"])
        ).fillna(False)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        close = pd.to_numeric(dataframe["close"], errors="coerce")
        atr = pd.to_numeric(dataframe["atr"], errors="coerce")
        high = pd.to_numeric(dataframe["high"], errors="coerce")
        low = pd.to_numeric(dataframe["low"], errors="coerce")

        long_entry_close = close.where(dataframe["enter_long"].astype(bool)).shift(1).ffill()
        long_entry_atr = atr.where(dataframe["enter_long"].astype(bool)).shift(1).ffill()
        short_entry_close = close.where(dataframe["enter_short"].astype(bool)).shift(1).ffill()
        short_entry_atr = atr.where(dataframe["enter_short"].astype(bool)).shift(1).ffill()
        take_profit_distance = float(self.take_profit_distance.value)
        stop_multiplier = float(self.atr_stop_multiplier.value)

        dataframe["exit_long"] = (
            (high >= long_entry_close + take_profit_distance)
            | (low <= long_entry_close - long_entry_atr * stop_multiplier)
        ).fillna(False)
        dataframe["exit_short"] = (
            (low <= short_entry_close - take_profit_distance)
            | (high >= short_entry_close + short_entry_atr * stop_multiplier)
        ).fillna(False)
        return dataframe

    def order_filled(
        self, pair: str, trade: Trade, order: Order, current_time, **kwargs
    ) -> None:
        if order.ft_order_side != trade.entry_side:
            return
        if self.dp is None:
            raise RuntimeError("Data provider is required to initialize Gold trade exits")

        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        prior_candles = dataframe.loc[dataframe["date"] < current_time]
        if prior_candles.empty:
            raise ValueError("No signal candle is available for the filled Gold entry")

        signal_candle = prior_candles.iloc[-1]
        signal_column = "enter_short" if trade.is_short else "enter_long"
        if not bool(signal_candle.get(signal_column, False)):
            raise ValueError("Filled Gold entry has no matching signal candle")

        signal_close = float(signal_candle["close"])
        signal_atr = float(signal_candle["atr"])
        if not math.isfinite(signal_close) or not math.isfinite(signal_atr) or signal_atr <= 0:
            raise ValueError("Gold signal candle contains an invalid close or ATR")

        take_profit_distance = float(self.take_profit_distance.value)
        take_profit = signal_close - take_profit_distance if trade.is_short else (
            signal_close + take_profit_distance
        )
        stop_distance = signal_atr * float(self.atr_stop_multiplier.value)
        stop_loss = (
            signal_close + stop_distance if trade.is_short else signal_close - stop_distance
        )
        trade.set_custom_data("gold_scalping_take_profit", take_profit)
        trade.set_custom_data("gold_scalping_stop_loss", stop_loss)

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float:
        stop_loss = self._trade_level(trade, "gold_scalping_stop_loss")
        return stoploss_from_absolute(
            stop_loss,
            current_rate,
            is_short=trade.is_short,
            leverage=trade.leverage,
        )

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> str | None:
        take_profit = self._trade_level(trade, "gold_scalping_take_profit")
        reached_target = (
            current_rate <= take_profit if trade.is_short else current_rate >= take_profit
        )
        return "gold_take_profit" if reached_target else None

    @staticmethod
    def _trade_level(trade: Trade, key: str) -> float:
        value = trade.get_custom_data(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"Gold trade is missing its valid {key} level")
        return float(value)
