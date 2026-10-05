from decimal import Decimal

import pandas as pd
import pytest

from freqtrade.forex.strategy_hyperopt import run_strategy_hyperopt
from freqtrade.forex.models import OandaInstrument
from freqtrade.forex.runner import DryRunPortfolioWorker, DryRunWorker, WorkerConfig
from freqtrade.forex.strategy_loop import Signal, StrategyStepResult
from freqtrade.forex.strategy_execution import (
    FreqtradeStrategyAdapter,
    load_strategy,
    strategy_informative_timeframes,
)
from freqtrade.forex.strategy_catalog import validate_strategy_upload


def test_strategy_upload_rejects_duplicate_classes_within_file() -> None:
    source = "from freqtrade.strategy import IStrategy\nclass Duplicate(IStrategy): pass\nclass Duplicate(IStrategy): pass\n"
    with pytest.raises(ValueError, match="unique within the file"):
        validate_strategy_upload("Duplicate.py", source)


def test_missing_strategy_error_lists_discovered_class_names() -> None:
    from freqtrade.forex.strategy_execution import load_strategy

    with pytest.raises(ValueError, match="Available classes:") as exc_info:
        load_strategy("MyUnknownStrategy", "1h", "EUR/USD")
    assert "ForexEmaStrategy" in str(exc_info.value)
    assert "ForexSampleStrategy" in str(exc_info.value)


def test_strategy_loader_applies_pair_timeframe_and_checks_informatives() -> None:
    strategy = load_strategy("ForexEmaStrategy", "1h", "EUR/USD")

    assert strategy.timeframe == "1h"
    assert strategy.config["timeframe"] == "1h"
    assert strategy_informative_timeframes(strategy, "EUR/USD") == ("4h",)
    with pytest.raises(ValueError, match="Informative timeframe must be equal to or higher"):
        load_strategy("ForexEmaStrategy", "1d", "EUR/USD")


def test_live_ema_uses_forming_four_hour_close_without_changing_history() -> None:
    strategy = load_strategy("ForexEmaStrategy", "5m", "EUR/USD")
    base_dates = pd.date_range("2026-01-01", periods=2000, freq="5min", tz="UTC")
    base_closes = [1.1 + index * 0.00001 for index in range(len(base_dates))]
    base = pd.DataFrame({
        "date": base_dates,
        "open": base_closes,
        "high": [value + 0.0001 for value in base_closes],
        "low": [value - 0.0001 for value in base_closes],
        "close": base_closes,
        "volume": [0.0] * len(base_dates),
    })
    informative_dates = pd.date_range(
        "2026-01-01", periods=41, freq="4h", tz="UTC"
    )
    informative_closes = [1.0 + index * 0.001 for index in range(41)]
    informative = pd.DataFrame({
        "date": informative_dates,
        "open": informative_closes,
        "high": [value + 0.001 for value in informative_closes],
        "low": [value - 0.001 for value in informative_closes],
        "close": informative_closes,
        "volume": [0.0] * len(informative_dates),
    })
    forming = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-07T20:00:00Z")],
        "open": [1.2],
        "high": [1.3],
        "low": [1.1],
        "close": [1.25],
        "volume": [0.0],
    })
    adapter = FreqtradeStrategyAdapter(
        strategy, "EUR/USD", {"4h": informative}
    )
    closed_result = adapter._populate(base)
    closed_fast = float(closed_result["ema_fast_4h"].iloc[-1])

    adapter.update_informative_candles(
        {"4h": informative}, {"4h": forming}
    )
    live_result = adapter._populate(base)
    expected_fast = (2.0 / 21.0) * 1.25 + (1.0 - 2.0 / 21.0) * closed_fast
    assert live_result["ema_fast_4h"].iloc[-1] == pytest.approx(expected_fast)
    assert live_result["htf_bias"].iloc[-1] == 1
    assert live_result["ema_fast_4h"].iloc[-2] == pytest.approx(
        closed_result["ema_fast_4h"].iloc[-2]
    )

    bearish_closes = (
        [1.0 + index * 0.001 for index in range(60)]
        + [1.059 - (index + 1) * 0.001 for index in range(40)]
    )
    bearish_dates = pd.date_range(
        end=informative_dates[-1], periods=len(bearish_closes), freq="4h", tz="UTC"
    )
    bearish_informative = pd.DataFrame({
        "date": bearish_dates,
        "open": bearish_closes,
        "high": [value + 0.001 for value in bearish_closes],
        "low": [value - 0.001 for value in bearish_closes],
        "close": bearish_closes,
        "volume": [0.0] * len(bearish_closes),
    })
    bearish_forming = forming.assign(close=bearish_closes[-1] - 0.0005)
    adapter.update_informative_candles(
        {"4h": bearish_informative}, {"4h": bearish_forming}
    )
    bearish_live_result = adapter._populate(base)
    assert bearish_live_result["htf_bias"].iloc[-1] == -1


def test_higher_timeframe_adx_threshold_filters_weak_trend() -> None:
    strategy = load_strategy("ForexEmaStrategy", "5m", "EUR/USD")
    index = range(40)
    weak = pd.DataFrame({
        "close_4h": [1.1] * 40,
        "ema_fast_4h": [1.09] * 40,
        "ema_slow_4h": [1.08] * 40,
        "ema_fast_previous_4h": [1.08] * 40,
        "adx_4h": [10.0] * 40,
    }, index=index)
    strategy.informative_adx_min_opt.value = 20
    assert not bool(strategy._closed_higher_timeframe_bias(weak).any())
    weak["adx_4h"] = 25.0
    assert bool((strategy._closed_higher_timeframe_bias(weak) == 1).all())


def test_ema_strategy_requires_lower_timeframe_setup_in_htf_direction() -> None:
    strategy = load_strategy("ForexEmaStrategy", "5m", "EUR/USD")
    bullish = pd.DataFrame({
        "close": [1.1] * 84 + [1.099, 1.101],
        "fast_ema": [1.100] * 86,
        "slow_ema": [1.099] * 86,
        "rsi": [60.0] * 86,
        "htf_bias": [1] * 86,
    })
    long_signals = strategy.populate_entry_trend(bullish, {"pair": "EUR/USD"})
    assert bool(long_signals.iloc[-1]["enter_long"])
    assert not bool(long_signals.iloc[-1]["enter_short"])

    bullish["htf_bias"] = -1
    blocked_long = strategy.populate_entry_trend(bullish, {"pair": "EUR/USD"})
    assert not bool(blocked_long.iloc[-1]["enter_long"])

    bearish = pd.DataFrame({
        "close": [1.1] * 84 + [1.101, 1.099],
        "fast_ema": [1.100] * 86,
        "slow_ema": [1.101] * 86,
        "rsi": [40.0] * 86,
        "htf_bias": [-1] * 86,
    })
    short_signals = strategy.populate_entry_trend(bearish, {"pair": "EUR/USD"})
    assert bool(short_signals.iloc[-1]["enter_short"])
    assert not bool(short_signals.iloc[-1]["enter_long"])


def test_precomputed_ema_backtest_matches_prefix_only_signals() -> None:
    strategy = load_strategy("ForexEmaStrategy", "5m", "EUR/USD")
    dates = pd.date_range("2026-01-01", periods=240, freq="5min", tz="UTC")
    close = [
        1.1 + index * 0.00001 + ((index % 18) - 9) * 0.00008
        for index in range(len(dates))
    ]
    candles = pd.DataFrame({
        "date": dates,
        "open": close,
        "high": [value + 0.0001 for value in close],
        "low": [value - 0.0001 for value in close],
        "close": close,
        "volume": [0.0] * len(close),
    })
    informative_dates = pd.date_range(
        "2025-12-20", periods=110, freq="4h", tz="UTC"
    )
    informative_close = [
        1.0 + index * 0.0002 + (0.002 if index % 30 > 15 else 0.0)
        for index in range(len(informative_dates))
    ]
    informative = pd.DataFrame({
        "date": informative_dates,
        "open": informative_close,
        "high": [value + 0.001 for value in informative_close],
        "low": [value - 0.001 for value in informative_close],
        "close": informative_close,
        "volume": [0.0] * len(informative_close),
    })
    prepared = FreqtradeStrategyAdapter(strategy, "EUR/USD", {"4h": informative})
    prepared.prepare_backtest(candles)

    for end in (85, 120, 180, len(candles)):
        prefix = candles.iloc[:end]
        reference = FreqtradeStrategyAdapter(
            load_strategy("ForexEmaStrategy", "5m", "EUR/USD"),
            "EUR/USD",
            {"4h": informative},
        )
        assert prepared.signal(prefix) is reference.signal(prefix)
        for direction in (Signal.LONG, Signal.SHORT):
            assert prepared.exit_signal(prefix, direction) == reference.exit_signal(
                prefix, direction
            )


def test_builtin_ema_strategy_exposes_optimizer_parameters() -> None:
    dates = pd.date_range("2026-01-01", periods=60, freq="5min", tz="UTC")
    closes = [1.1 + ((index % 12) - 6) * 0.0002 for index in range(len(dates))]
    candles = pd.DataFrame({
        "date": dates,
        "open": closes,
        "high": [value + 0.001 for value in closes],
        "low": [value - 0.001 for value in closes],
        "close": closes,
        "volume": [0.0] * len(dates),
    })
    informative_dates = pd.date_range(
        "2025-12-31", periods=12, freq="4h", tz="UTC"
    )
    informative_closes = [1.1 + index * 0.0001 for index in range(12)]
    informative = pd.DataFrame({
        "date": informative_dates,
        "open": informative_closes,
        "high": informative_closes,
        "low": informative_closes,
        "close": informative_closes,
        "volume": [0.0] * len(informative_dates),
    })
    candidates = run_strategy_hyperopt(
        candles,
        {"4h": informative},
        OandaInstrument(
            name="EUR_USD",
            display_name="EUR/USD",
            pip_location=-4,
            display_precision=5,
            trade_units_precision=0,
            minimum_trade_size=Decimal("1"),
        ),
        pair="EUR/USD",
        strategy_class="ForexEmaStrategy",
        timeframe="5m",
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        max_attempts=2,
        hyperopt_loss="ProfitDrawDownHyperOptLoss",
    )
    assert len(candidates) == 2
    assert all(
        set(candidate["parameters"]) == {
            "fast_period_opt",
            "slow_period_opt",
            "informative_fast_period_opt",
            "informative_slow_period_opt",
            "informative_adx_min_opt",
            "entry_rsi_opt",
        }
        for candidate in candidates
    )


def test_informative_strategy_loads_and_hyperopts_with_daily_candles(monkeypatch, tmp_path) -> None:
    strategy_directory = tmp_path / "strategies"
    strategy_directory.mkdir()
    strategy_file = strategy_directory / "DailyGateStrategy.py"
    strategy_file.write_text(
        """from freqtrade.strategy import IStrategy, IntParameter, informative

class DailyGateStrategy(IStrategy):
    timeframe = '5m'
    startup_candle_count = 2
    window = IntParameter(2, 3, default=2, space='buy')

    @informative('1d')
    def populate_indicators_1d(self, dataframe, metadata):
        dataframe['daily_reference'] = dataframe['close']
        return dataframe

    def populate_indicators(self, dataframe, metadata):
        dataframe['average'] = dataframe['close'].rolling(self.window.value, min_periods=1).mean()
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe['enter_long'] = (dataframe['close'] > dataframe['average']) & (dataframe['daily_reference_1d'] > 0)
        dataframe['enter_short'] = False
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe['exit_long'] = dataframe['close'] < dataframe['average']
        dataframe['exit_short'] = False
        return dataframe
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("FOREX_STRATEGIES_DIR", str(strategy_directory))
    strategy = load_strategy("DailyGateStrategy", "5m", "EUR/USD")
    assert strategy_informative_timeframes(strategy, "EUR/USD") == ("1d",)

    dates = pd.date_range("2026-01-01", periods=80, freq="5min", tz="UTC")
    candles = pd.DataFrame({
        "date": dates,
        "open": [1.1 + index * 0.0001 for index in range(len(dates))],
        "high": [1.2] * len(dates),
        "low": [1.0] * len(dates),
        "close": [1.1 + index * 0.0001 for index in range(len(dates))],
        "volume": [0.0] * len(dates),
    })
    informative = pd.DataFrame({
        "date": pd.to_datetime(["2025-12-31T00:00:00Z", "2026-01-01T00:00:00Z"]),
        "open": [1.0, 1.0],
        "high": [1.2, 1.2],
        "low": [0.9, 0.9],
        "close": [1.1, 1.2],
        "volume": [0.0, 0.0],
    })
    adapter = FreqtradeStrategyAdapter(strategy, "EUR/USD", {"1d": informative})
    assert adapter.signal(candles) in {"long", "short", "flat"}

    candidates = run_strategy_hyperopt(
        candles,
        {"1d": informative},
        OandaInstrument(
            name="EUR_USD",
            display_name="EUR/USD",
            pip_location=-4,
            display_precision=5,
            trade_units_precision=0,
            minimum_trade_size=Decimal("1"),
        ),
        pair="EUR/USD",
        strategy_class="DailyGateStrategy",
        timeframe="5m",
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        max_attempts=2,
        hyperopt_loss="ProfitDrawDownHyperOptLoss",
    )
    assert len(candidates) == 2
    assert all(set(candidate["parameters"]) == {"window"} for candidate in candidates)
def test_one_portfolio_cycle_runs_every_due_pair(tmp_path) -> None:
    class RecordingLoop:
        def __init__(self, pair: str) -> None:
            self.pair = pair
            self.calls: list[str] = []

        async def step(self, pair: str, timeframe: str, *, candle_count: int) -> StrategyStepResult:
            self.calls.append(pair)
            return StrategyStepResult(Signal.FLAT, None)

    loops = [RecordingLoop("EUR/USD"), RecordingLoop("GBP/USD")]
    workers = tuple(
        DryRunWorker(
            loop,
            WorkerConfig(
                pair=pair,
                timeframe="5m",
                interval_seconds=300,
                runtime_state_path=str(tmp_path / f"{pair}.json"),
            ),
        )
        for loop, pair in zip(loops, ("EUR/USD", "GBP/USD"), strict=True)
    )

    import asyncio

    completed = asyncio.run(DryRunPortfolioWorker(workers).run(max_steps=1))

    assert completed == 2
    assert [loop.calls for loop in loops] == [["EUR/USD"], ["GBP/USD"]]
