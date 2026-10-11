"""Gold scalping strategy translated from the Pine Script entry and risk rules."""

import math

import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Order, Trade
from freqtrade.strategy import IStrategy, stoploss_from_absolute


class GoldScalpingStrategy(IStrategy):
    """Trade engulfing candles in the EMA trend with RSI and ATR risk controls."""

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "15m"
    startup_candle_count = 200
    process_only_new_candles = True
    use_exit_signal = True
    use_custom_stoploss = True
    minimal_roi: dict[str, float] = {}
    stoploss = -0.99

    rsi_period = 14
    atr_period = 14
    take_profit_distance = 2.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        close = pd.to_numeric(dataframe["close"], errors="coerce")
        high = pd.to_numeric(dataframe["high"], errors="coerce")
        low = pd.to_numeric(dataframe["low"], errors="coerce")

        dataframe["ema50"] = close.ewm(span=50, adjust=False, min_periods=50).mean()
        dataframe["ema200"] = close.ewm(span=200, adjust=False, min_periods=200).mean()

        previous_close = close.shift(1)
        true_range = pd.concat(
            (
                high - low,
                (high - previous_close).abs(),
                (low - previous_close).abs(),
            ),
            axis=1,
        ).max(axis=1)
        dataframe["atr"] = true_range.ewm(
            alpha=1.0 / self.atr_period,
            adjust=False,
            min_periods=self.atr_period,
        ).mean()

        change = close.diff()
        average_gain = change.clip(lower=0.0).ewm(
            alpha=1.0 / self.rsi_period,
            adjust=False,
            min_periods=self.rsi_period,
        ).mean()
        average_loss = (-change.clip(upper=0.0)).ewm(
            alpha=1.0 / self.rsi_period,
            adjust=False,
            min_periods=self.rsi_period,
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
        rsi_in_range = dataframe["rsi"].between(45.0, 55.0)

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

        dataframe["exit_long"] = (
            (high >= long_entry_close + self.take_profit_distance)
            | (low <= long_entry_close - long_entry_atr)
        ).fillna(False)
        dataframe["exit_short"] = (
            (low <= short_entry_close - self.take_profit_distance)
            | (high >= short_entry_close + short_entry_atr)
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

        take_profit = (
            signal_close - self.take_profit_distance
            if trade.is_short
            else signal_close + self.take_profit_distance
        )
        stop_loss = signal_close + signal_atr if trade.is_short else signal_close - signal_atr
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
