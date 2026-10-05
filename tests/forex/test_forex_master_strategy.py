import pandas as pd

from freqtrade.forex.strategy_execution import load_strategy


def test_forex_master_matches_pine_crossover_signals() -> None:
    strategy = load_strategy("ForexMasterStrategy", "15m", "EUR/USD")
    dataframe = pd.DataFrame(
        {
            "close": [9.0, 9.0, 11.0, 9.0],
            "lower": [10.0, 10.0, 10.0, 10.0],
            "upper": [10.0, 10.0, 10.0, 10.0],
            "adx_fast": [1.0, 1.0, 1.0, 1.0],
            "adx_slow": [2.0, 2.0, 2.0, 2.0],
        }
    )

    result = strategy.populate_entry_trend(dataframe, {"pair": "EUR/USD"})

    assert result["enter_long"].tolist() == [False, False, True, False]
    assert result["enter_short"].tolist() == [False, False, False, True]


def test_forex_master_requires_adx_condition() -> None:
    strategy = load_strategy(
        "ForexMasterStrategy",
        "15m",
        "EUR/USD",
    )
    dataframe = pd.DataFrame(
        {
            "close": [9.0, 9.0, 11.0, 9.0],
            "lower": [10.0, 10.0, 10.0, 10.0],
            "upper": [10.0, 10.0, 10.0, 10.0],
            "adx_fast": [1.0, 1.0, 3.0, 1.0],
            "adx_slow": [2.0, 2.0, 2.0, 2.0],
        }
    )

    result = strategy.populate_entry_trend(dataframe, {"pair": "EUR/USD"})

    assert not bool(result["enter_long"].iloc[2])
    assert bool(result["enter_short"].iloc[3])


def test_forex_master_exposes_indicator_parameters_for_hyperopt() -> None:
    strategy = load_strategy("ForexMasterStrategy", "15m", "EUR/USD")

    assert strategy.stoploss == -0.50
    assert strategy.use_exit_signal
    assert strategy.minimal_roi == {
        "0": 0.0005,
        "60": 0.0004,
        "180": 0.0003,
        "360": 0.0002,
        "720": 0.0,
    }
    assert strategy.band_length.value == 20
    assert strategy.band_multiplier.value == 1.5
    assert strategy.adx_length.value == 50
    assert strategy.adx_fast_ema_length.value == 6
    assert strategy.adx_slow_ema_length.value == 12
    assert strategy.exit_band_offset.value == 0.0
    assert all(
        getattr(strategy, name).optimize
        for name in (
            "band_length",
            "band_multiplier",
            "adx_length",
            "adx_fast_ema_length",
            "adx_slow_ema_length",
            "exit_band_offset",
        )
    )


def test_forex_master_calculates_population_standard_deviation_bands() -> None:
    strategy = load_strategy(
        "ForexMasterStrategy",
        "15m",
        "EUR/USD",
        parameter_values={"band_length": 2, "band_multiplier": 1.0},
    )
    dataframe = pd.DataFrame(
        {
            "open": [0.9, 1.9, 2.9],
            "high": [1.1, 2.1, 3.1],
            "low": [0.9, 1.9, 2.9],
            "close": [1.0, 2.0, 3.0],
            "volume": [0.0, 0.0, 0.0],
        }
    )

    result = strategy.populate_indicators(dataframe, {"pair": "EUR/USD"})

    assert result["basis"].iloc[-1] == 2.5
    assert result["std_dev"].iloc[-1] == 0.5
    assert result["upper"].iloc[-1] == 3.0
    assert result["lower"].iloc[-1] == 2.0
    assert result["adx_fast"].iloc[-1] == result["adx_slow"].iloc[-1]


def test_forex_master_exit_signal_crosses_opposite_band() -> None:
    strategy = load_strategy(
        "ForexMasterStrategy",
        "15m",
        "EUR/USD",
        parameter_values={"band_length": 2, "exit_band_offset": 0.0},
    )
    dataframe = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=3, freq="15min", tz="UTC"),
            "open": [1.0, 1.0, 1.0],
            "high": [1.0, 1.0, 1.1],
            "low": [0.8, 0.8, 0.9],
            "close": [0.9, 1.05, 1.15],
            "volume": [0.0, 0.0, 0.0],
        }
    )
    prepared = strategy.populate_indicators(dataframe, {"pair": "EUR/USD"})
    prepared.loc[:, "adx_fast"] = 0.0
    prepared.loc[:, "adx_slow"] = 0.0
    prepared = strategy.populate_exit_trend(prepared, {"pair": "EUR/USD"})

    assert not prepared["exit_long"].iloc[-1]
    assert not prepared["exit_short"].iloc[-1]
