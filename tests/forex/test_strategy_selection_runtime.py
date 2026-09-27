from decimal import Decimal

import pandas as pd
import pytest

from freqtrade.forex.ai_hyperopt import run_strategy_hyperopt
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
    candidates = run_strategy_hyperopt(
        candles,
        {},
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
    assert all(set(candidate["parameters"]) == {"fast_period_opt", "slow_period_opt"} for candidate in candidates)


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