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

    fast_ema_index = CategoricalParameter(
        list(range(3, 20)),
        default=3,
        space="buy",
    )
    slow_ema_index = CategoricalParameter(
        list(range(4, 22)),
        default=19,
        space="buy",
    )
    ema_timeperiod = CategoricalParameter(
        list(range(7, 22)),
        default=14,
        space="buy",
    )

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config or {})
        for parameter_name in ("fast_ema_index", "slow_ema_index", "ema_timeperiod"):
            setattr(self, parameter_name, deepcopy(getattr(type(self), parameter_name)))

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return add_chained_ema_indicators(
            dataframe,
            ema_count=20,
            timeperiod=int(self.ema_timeperiod.value),
        )

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        fast_ema = dataframe[f"ema_x_{self.fast_ema_index.value}"]
        slow_ema = dataframe[f"ema_x_{self.slow_ema_index.value}"]

        dataframe["enter_long"] = qtpylib.crossed_above(fast_ema, slow_ema).fillna(False)
        dataframe["enter_short"] = qtpylib.crossed_below(fast_ema, slow_ema).fillna(False)
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        fast_ema = dataframe[f"ema_x_{self.fast_ema_index.value}"]
        slow_ema = dataframe[f"ema_x_{self.slow_ema_index.value}"]

        dataframe["exit_long"] = qtpylib.crossed_below(fast_ema, slow_ema).fillna(False)
        dataframe["exit_short"] = qtpylib.crossed_above(fast_ema, slow_ema).fillna(False)
        return dataframe
