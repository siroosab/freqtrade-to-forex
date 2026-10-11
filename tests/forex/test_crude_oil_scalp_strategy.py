import pandas as pd

from freqtrade.forex.strategy_execution import load_strategy


def test_crude_oil_scalp_uses_pine_defaults_and_calculates_indicators() -> None:
    strategy = load_strategy("CrudeOilScalpStrategy", "3m", "WTI/USD")
    close = [100.0 + index for index in range(100)]
    dataframe = pd.DataFrame({"close": close})

    result = strategy.populate_indicators(dataframe, {"pair": "WTI/USD"})

    assert strategy.timeframe == "3m"
    assert strategy.rsi_length.value == 14
    assert strategy.rsi_high.value == 80
    assert strategy.rsi_low.value == 20
    assert strategy.sma_length.value == 70
    assert pd.isna(result["sma"].iloc[68])
    assert result["sma"].iloc[69] == sum(close[0:70]) / 70
    assert result["rsi"].iloc[-1] == 100.0


def test_crude_oil_scalp_entries_match_pine_buy_and_sell_conditions() -> None:
    strategy = load_strategy("CrudeOilScalpStrategy", "3m", "WTI/USD")
    dataframe = pd.DataFrame(
        {
            "open": [9.5, 10.5, 11.5, 10.5, 9.0, 8.5],
            "high": [10.0, 11.2, 12.5, 11.0, 9.5, 9.0],
            "low": [9.0, 10.0, 11.0, 10.0, 8.0, 7.5],
            "close": [10.0, 11.0, 12.0, 10.5, 8.5, 8.0],
            "sma": [10.0, 10.0, 9.0, 9.0, 9.0, 9.0],
        }
    )

    result = strategy.populate_entry_trend(dataframe, {"pair": "WTI/USD"})

    assert result["enter_long"].tolist() == [False, False, True, False, False, False]
    assert result["enter_short"].tolist() == [False, False, False, False, False, True]


def test_crude_oil_scalp_exits_on_both_pine_reversal_alerts() -> None:
    strategy = load_strategy(
        "CrudeOilScalpStrategy",
        "3m",
        "WTI/USD",
        parameter_values={"rsi_high": 80, "rsi_low": 20},
    )
    dataframe = pd.DataFrame(
        {
            "open": [10.0, 10.0, 10.0, 10.0, 9.0, 9.0],
            "close": [10.0, 10.0, 10.0, 10.0, 9.5, 9.5],
            "rsi": [85.0, 80.0, 79.0, 19.0, 20.0, 21.0],
        }
    )

    result = strategy.populate_exit_trend(dataframe, {"pair": "WTI/USD"})

    assert result["exit_long"].tolist() == [False, False, True, False, False, False]
    assert result["exit_short"].tolist() == [False, False, False, False, False, True]
