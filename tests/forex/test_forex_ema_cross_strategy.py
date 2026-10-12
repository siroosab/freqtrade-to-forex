import pandas as pd

from freqtrade.forex.strategy_execution import load_strategy


def test_ema_cross_uses_selected_ema_indices_for_entry_and_exit() -> None:
    strategy = load_strategy(
        "ForexEmaCrossStrategy",
        "15m",
        "EUR/USD",
        parameter_values={
            "entry_fast_ema_index": 4,
            "entry_slow_ema_index": 15,
            "entry_ema_timeperiod": 7,
            "exit_fast_ema_index": 3,
            "exit_slow_ema_index": 6,
            "exit_ema_timeperiod": 4,
        },
    )
    dataframe = pd.DataFrame(
        {
            "ema_x_4": [1.0, 1.0, 2.0, 1.0, 1.0],
            "ema_x_15": [1.5, 1.5, 1.5, 1.5, 1.5],
            "exit_ema_x_3": [1.0, 1.0, 1.0, 2.0, 1.0],
            "exit_ema_x_6": [1.5, 1.5, 1.5, 1.5, 1.5],
        }
    )

    entries = strategy.populate_entry_trend(dataframe.copy(), {"pair": "EUR/USD"})
    exits = strategy.populate_exit_trend(dataframe.copy(), {"pair": "EUR/USD"})

    assert entries["enter_long"].tolist() == [False, False, True, False, False]
    assert entries["enter_short"].tolist() == [False, False, False, True, False]
    assert exits["exit_long"].tolist() == [False, False, False, False, True]
    assert exits["exit_short"].tolist() == [False, False, False, True, False]


def test_ema_cross_exposes_separate_bounded_parameters_and_ema_chains() -> None:
    strategy = load_strategy("ForexEmaCrossStrategy", "15m", "EUR/USD")

    assert strategy.entry_fast_ema_index.opt_range == list(range(2, 8))
    assert strategy.entry_slow_ema_index.opt_range == list(range(8, 22))
    assert strategy.entry_ema_timeperiod.opt_range == list(range(7, 22))
    assert strategy.entry_ema_timeperiod.value == 14
    assert strategy.exit_fast_ema_index.opt_range == list(range(2, 5))
    assert strategy.exit_slow_ema_index.opt_range == list(range(5, 8))
    assert strategy.exit_ema_timeperiod.opt_range == list(range(2, 7))
    assert max(strategy.exit_fast_ema_index.opt_range) < min(
        strategy.entry_slow_ema_index.opt_range
    )
    assert max(strategy.exit_ema_timeperiod.opt_range) < min(
        strategy.entry_ema_timeperiod.opt_range
    )

    dataframe = pd.DataFrame({"close": [float(index) for index in range(100)]})
    result = strategy.populate_indicators(dataframe, {"pair": "EUR/USD"})

    assert "ema_x_2" in result
    assert "ema_x_7" in result
    assert "ema_x_8" in result
    assert "ema_x_21" in result
    assert "exit_ema_x_2" in result
    assert "exit_ema_x_7" in result
