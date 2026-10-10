from decimal import Decimal
import math

import pandas as pd
import pytest

from freqtrade.forex.models import OandaInstrument
from freqtrade.forex.strategy_execution import FreqtradeStrategyAdapter, load_strategy
from freqtrade.forex.strategy_hyperopt import hyperopt_worker_limit, run_strategy_hyperopt


@pytest.mark.skipif(hyperopt_worker_limit() < 2, reason="requires two available CPUs")
def test_strategy_hyperopt_evaluates_candidates_in_process_workers() -> None:
    closes = [1.1 + math.sin(index / 3) * 0.002 for index in range(100)]
    candles = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=len(closes), freq="15min", tz="UTC"),
            "open": closes,
            "high": [close + 0.001 for close in closes],
            "low": [close - 0.001 for close in closes],
            "close": closes,
            "volume": [0.0] * len(closes),
        }
    )

    candidates = run_strategy_hyperopt(
        candles,
        {},
        OandaInstrument("EUR_USD", "EUR/USD", -4, 5, 0, Decimal(1)),
        pair="EUR/USD",
        strategy_class="ForexMasterStrategy",
        timeframe="15m",
        starting_balance=Decimal(10000),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        max_attempts=2,
        hyperopt_loss="ProfitDrawDownHyperOptLoss",
        workers=2,
    )

    assert len(candidates) == 2
    assert all(candidate["coverage"] == 2 for candidate in candidates)


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


def test_forex_master_signal_series_matches_prefix_signals(monkeypatch) -> None:
    closes = [1.1 + math.sin(index / 3) * 0.002 for index in range(240)]
    candles = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=len(closes), freq="15min", tz="UTC"),
            "open": closes,
            "high": [close + 0.001 for close in closes],
            "low": [close - 0.001 for close in closes],
            "close": closes,
            "volume": [0.0] * len(closes),
        }
    )
    strategy = load_strategy("ForexMasterStrategy", "15m", "EUR/USD")
    original_populate_indicators = strategy.populate_indicators
    populated_batches = 0

    def count_populations(dataframe, metadata):
        nonlocal populated_batches
        populated_batches += 1
        return original_populate_indicators(dataframe, metadata)

    monkeypatch.setattr(strategy, "populate_indicators", count_populations)
    adapter = FreqtradeStrategyAdapter(strategy, "EUR/USD")
    actual = adapter.signal_series(candles)

    for end in (80, 150, len(candles)):
        expected = FreqtradeStrategyAdapter(
            load_strategy("ForexMasterStrategy", "15m", "EUR/USD"),
            "EUR/USD",
        ).signal(candles.iloc[:end])
        assert actual[end - 1] is expected
    assert populated_batches == 1


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
