from decimal import Decimal

import pandas as pd

from freqtrade.forex.models import OandaInstrument
from freqtrade.forex.strategy_execution import load_strategy
from freqtrade.forex.strategy_hyperopt import run_strategy_hyperopt


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
    assert all(
        getattr(strategy, name).optimize
        for name in ("rsi_length", "rsi_high", "rsi_low", "sma_length")
    )
    assert strategy.rsi_low.high < strategy.rsi_high.low
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


def test_crude_oil_scalp_hyperopt_samples_all_declared_parameters() -> None:
    close = [70.0 + (index % 7) * 0.2 for index in range(100)]
    candles = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=100, freq="3min", tz="UTC"),
            "open": close,
            "high": [price + 0.2 for price in close],
            "low": [price - 0.2 for price in close],
            "close": close,
            "volume": [0.0] * len(close),
        }
    )

    candidates = run_strategy_hyperopt(
        candles,
        {},
        OandaInstrument("WTI_USD", "WTI/USD", -2, 3, 0, 1),
        pair="WTI/USD",
        strategy_class="CrudeOilScalpStrategy",
        timeframe="3m",
        starting_balance=Decimal(10000),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.01"),
        max_attempts=2,
        hyperopt_loss="ForexWinRateProfitHyperOptLoss",
    )

    expected_parameters = {"rsi_length", "rsi_high", "rsi_low", "sma_length"}
    assert len(candidates) == 2
    assert all(set(candidate["parameters"]) == expected_parameters for candidate in candidates)
    for candidate in candidates:
        assert 1 <= candidate["parameters"]["rsi_low"] <= 49
        assert 51 <= candidate["parameters"]["rsi_high"] <= 99
        assert candidate["parameters"]["rsi_low"] < candidate["parameters"]["rsi_high"]
        assert 2 <= candidate["parameters"]["rsi_length"] <= 200
        assert 3 <= candidate["parameters"]["sma_length"] <= 500
        assert "validationWinRate" in candidate
