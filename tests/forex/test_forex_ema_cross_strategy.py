import pandas as pd

from freqtrade.forex.strategy_execution import load_strategy


def test_ema_cross_uses_selected_ema_indices_for_entry_and_exit() -> None:
    strategy = load_strategy(
        "ForexEmaCrossStrategy",
        "15m",
        "EUR/USD",
        parameter_values={
            "fast_ema_index": 4,
            "slow_ema_index": 15,
            "ema_timeperiod": 7,
        },
    )
    dataframe = pd.DataFrame(
        {
            "ema_x_4": [1.0, 1.0, 2.0, 1.0, 1.0],
            "ema_x_15": [1.5, 1.5, 1.5, 1.5, 1.5],
            "ema_x_3": [1.0, 2.0, 1.0, 2.0, 1.0],
            "ema_x_19": [1.5, 1.5, 1.5, 1.5, 1.5],
        }
    )

    entries = strategy.populate_entry_trend(dataframe.copy(), {"pair": "EUR/USD"})
    exits = strategy.populate_exit_trend(dataframe.copy(), {"pair": "EUR/USD"})

    assert entries["enter_long"].tolist() == [False, False, True, False, False]
    assert entries["enter_short"].tolist() == [False, False, False, True, False]
    assert exits["exit_long"].tolist() == [False, False, False, True, False]
    assert exits["exit_short"].tolist() == [False, False, True, False, False]


def test_ema_cross_exposes_limited_categorical_parameters_and_chained_emas() -> None:
    strategy = load_strategy("ForexEmaCrossStrategy", "15m", "EUR/USD")

    assert strategy.fast_ema_index.opt_range == list(range(3, 20))
    assert strategy.slow_ema_index.opt_range == list(range(4, 22))
    assert strategy.ema_timeperiod.opt_range == list(range(7, 22))
    assert strategy.ema_timeperiod.value == 14

    dataframe = pd.DataFrame({"close": [float(index) for index in range(100)]})
    result = strategy.populate_indicators(dataframe, {"pair": "EUR/USD"})

    assert "ema_x_3" in result
    assert "ema_x_19" in result
    assert "ema_x_21" in result
