"""Forex strategy based on chained EMA crossovers."""

from copy import deepcopy

from pandas import DataFrame
from technical import qtpylib

from freqtrade.forex.features import add_chained_ema_indicators
from freqtrade.strategy import CategoricalParameter, IStrategy


class ForexEmaCrossStrategy(IStrategy):
    """Enter on fast/slow chained EMA crosses and exit on the reverse cross."""

    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "15m"
    startup_candle_count = 400
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

    entry_fast_ema_index = CategoricalParameter(
        list(range(5, 7)),
        default=6,
        space="buy",
    )
    entry_slow_ema_index = CategoricalParameter(
        list(range(19, 21)),
        default=20,
        space="buy",
    )
    entry_ema_timeperiod = CategoricalParameter(  #ema_timeperiod
        list(range(7, 9)),
        default=8,
        space="buy",
    )
    exit_fast_ema_index = CategoricalParameter(
        list(range(3, 5)),
        default=4,
        space="sell",
    )
    exit_slow_ema_index = CategoricalParameter(
        list(range(7, 9)),
        default=8,
        space="sell",
    )
    exit_ema_timeperiod = CategoricalParameter(  #ema_timeperiod
        list(range(4, 6)),
        default=5,
        space="sell",
    )

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config or {})
        for parameter_name in (
            "entry_fast_ema_index",
            "entry_slow_ema_index",
            "entry_ema_timeperiod",
            "exit_fast_ema_index",
            "exit_slow_ema_index",
            "exit_ema_timeperiod",
        ):
            setattr(self, parameter_name, deepcopy(getattr(type(self), parameter_name)))

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        add_chained_ema_indicators(
            dataframe,
            ema_count=30,
            timeperiod=int(self.entry_ema_timeperiod.value),
        )
        exit_indicators = add_chained_ema_indicators(
            dataframe[["close"]].copy(),
            ema_count=20,
            timeperiod=int(self.exit_ema_timeperiod.value),
        )
        for index in range(2, 22):
            dataframe[f"exit_ema_x_{index}"] = exit_indicators[f"ema_x_{index}"]
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        fast_ema = dataframe[f"ema_x_{self.entry_fast_ema_index.value}"]
        slow_ema = dataframe[f"ema_x_{self.entry_slow_ema_index.value}"]
        #test mode##
        #dataframe["enter_long"] = qtpylib.crossed_above(fast_ema, slow_ema).fillna(False)
        #dataframe["enter_short"] = qtpylib.crossed_below(fast_ema, slow_ema).fillna(False)
        dataframe["enter_long"] = (
            (dataframe["count_low"] > 0)
            & dataframe["count_low"]
            .shift(1)
            .rolling(5)
            .apply(lambda x: (x == 0).all(), raw=True)
            .eq(1)
            & (dataframe["close"] > fast_ema)
        )

        dataframe["enter_short"] = (
            (dataframe["count_hi"] > 0)
            & dataframe["count_hi"]
            .shift(1)
            .rolling(5)
            .apply(lambda x: (x == 0).all(), raw=True)
            .eq(1)
        ) & (dataframe["close"] < fast_ema)

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        fast_ema = dataframe[f"exit_ema_x_{self.exit_fast_ema_index.value}"]
        slow_ema = dataframe[f"exit_ema_x_{self.exit_slow_ema_index.value}"]

        dataframe["exit_long"] = qtpylib.crossed_below(fast_ema, slow_ema).fillna(False)
        dataframe["exit_short"] = qtpylib.crossed_above(fast_ema, slow_ema).fillna(False)
        return dataframe
