import pandas as pd

from freqtrade.forex.strategy_execution import load_strategy


def test_atr_psar_enters_after_nth_trend_bar_and_exits_on_reversal() -> None:
    strategy = load_strategy(
        "ForexAtrPsarStrategy",
        "15m",
        "EUR/USD",
        parameter_values={
            "atr_length": 2,
            "psar_start": 0.02,
            "psar_increment": 0.02,
            "psar_maximum": 0.2,
            "entry_bars": 2,
        },
    )
    dataframe = pd.DataFrame(
        {
            "open": [10, 11, 12, 13, 14, 13, 12, 11, 15],
            "high": [11, 12, 13, 14, 15, 14, 13, 12, 17],
            "low": [9, 10, 11, 12, 13, 11, 10, 9, 8],
            "close": [10.5, 11.5, 12.5, 13.5, 14.5, 11.5, 9.5, 9.5, 16],
        }
    )

    indicators = strategy.populate_indicators(dataframe, {"pair": "EUR/USD"})
    entries = strategy.populate_entry_trend(indicators.copy(), {"pair": "EUR/USD"})
    exits = strategy.populate_exit_trend(indicators.copy(), {"pair": "EUR/USD"})

    assert entries["enter_long"].tolist() == [
        False, False, True, False, False, False, False, False, False
    ]
    assert entries["enter_short"].tolist() == [
        False, False, False, False, False, False, False, True, False
    ]
    assert exits["exit_long"].tolist() == [
        False, False, False, False, False, False, True, False, False
    ]
    assert exits["exit_short"].tolist() == [
        False, False, False, False, False, False, False, False, True
    ]


def test_atr_psar_hyperopt_parameters_have_expected_defaults_and_bounds() -> None:
    strategy = load_strategy("ForexAtrPsarStrategy", "15m", "EUR/USD")

    assert strategy.atr_length.value == 14
    assert (strategy.atr_length.low, strategy.atr_length.high) == (5, 30)
    assert strategy.entry_bars.value == 1
    assert (strategy.entry_bars.low, strategy.entry_bars.high) == (1, 10)
    assert strategy.psar_start.value == 0.02
    assert (strategy.psar_start.low, strategy.psar_start.high) == (0.01, 0.05)
    assert strategy.psar_increment.value == 0.02
    assert (strategy.psar_increment.low, strategy.psar_increment.high) == (0.005, 0.05)
    assert strategy.psar_maximum.value == 0.2
    assert (strategy.psar_maximum.low, strategy.psar_maximum.high) == (0.05, 0.5)
    assert strategy.can_short
    assert strategy.use_exit_signal
