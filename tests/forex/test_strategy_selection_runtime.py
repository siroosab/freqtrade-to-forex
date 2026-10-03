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


def test_missing_strategy_error_lists_discovered_class_names() -> None:
    from freqtrade.forex.strategy_execution import load_strategy

    with pytest.raises(ValueError, match="Available classes:.*ForexAIStrategyBaseline.*ForexEmaStrategy"):
        load_strategy("MyFreqAIStrategy", "1h", "EUR/USD")


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
        "high": informative_closes,
        "low": informative_closes,
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
    closed_ema = float(closed_result["ema_4h_4h"].iloc[-1])

    adapter.update_informative_candles(
        {"4h": informative}, {"4h": forming}
    )
    live_result = adapter._populate(base)
    expected = (
        (2.0 / 21.0) * 1.25
        + (1.0 - 2.0 / 21.0) * closed_ema
    )
    assert live_result["ema_4h_4h"].iloc[-1] == pytest.approx(expected)
    assert live_result["ema_4h_4h"].iloc[-2] == pytest.approx(
        closed_result["ema_4h_4h"].iloc[-2]
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


def test_freqai_dataset_uses_strategy_features_target_and_feature_settings(monkeypatch, tmp_path) -> None:
    from freqtrade.forex.cli import _build_freqai_strategy_features
    from freqtrade.forex.ai_dataset import build_forex_ai_dataset

    strategy_directory = tmp_path / "strategies"
    strategy_directory.mkdir()
    (strategy_directory / "ConfiguredFreqAIStrategy.py").write_text(
        """from freqtrade.strategy import IStrategy, IntParameter

class ConfiguredFreqAIStrategy(IStrategy):
    signal_threshold = IntParameter(1, 3, default=2, space='buy')

    def feature_engineering_expand_all(self, dataframe, period, metadata, **kwargs):
        dataframe[f"%-close-{period}"] = dataframe['close'].rolling(period, min_periods=1).mean()
        return dataframe

    def feature_engineering_standard(self, dataframe, metadata, **kwargs):
        dataframe['%-hour'] = dataframe['date'].dt.hour
        return dataframe

    def set_freqai_targets(self, dataframe, metadata, **kwargs):
        horizon = self.freqai_info['feature_parameters']['label_period_candles']
        dataframe['&-future-return'] = dataframe['close'].shift(-horizon) / dataframe['close'] - 1
        return dataframe

    def populate_indicators(self, dataframe, metadata):
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        return dataframe
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("FOREX_STRATEGIES_DIR", str(strategy_directory))
    from freqtrade.forex.strategy_execution import load_strategy

    freqai_config = {
        "enabled": True,
        "identifier": "test-run",
        "feature_parameters": {
            "label_period_candles": 3,
            "indicator_periods_candles": [2, 4],
            "include_shifted_candles": 1,
            "include_timeframes": ["5m"],
            "include_corr_pairlist": [],
            "principal_component_analysis": False,
        },
    }
    strategy = load_strategy(
        "ConfiguredFreqAIStrategy", "5m", "EUR/USD",
        config_overrides={"freqai": freqai_config},
    )
    strategy.freqai_info = freqai_config
    dates = pd.date_range("2026-01-01", periods=50, freq="5min", tz="UTC")
    close = [1.1 + index * 0.0001 for index in range(50)]
    candles = pd.DataFrame({
        "date": dates,
        "open": close,
        "high": [value + 0.001 for value in close],
        "low": [value - 0.001 for value in close],
        "close": close,
        "volume": [100.0] * len(close),
    })

    feature_frame, target_column = _build_freqai_strategy_features(
        candles,
        strategy,
        pair="EUR/USD",
        timeframe="5m",
        freqai_config=freqai_config,
    )
    dataset, manifest = build_forex_ai_dataset(
        candles,
        pair="EUR/USD",
        timeframe="M5",
        strategy_features=feature_frame,
        target_column=target_column,
        label_period=3,
        indicator_periods=(2, 4),
        include_shifted_candles=1,
    )

    assert target_column == "&-future-return"
    assert "%-close-2" in manifest.feature_columns
    assert any(column.endswith("_shift-1") for column in manifest.feature_columns)
    assert manifest.label_period == 3
    assert len(dataset) == 47


def test_lightgbm_classifier_trains_on_categorical_strategy_targets() -> None:
    from freqtrade.forex.ai_dataset import build_forex_ai_dataset
    from freqtrade.forex.ai_lgbm import LightGBMDirectionClassifier

    count = 120
    dates = pd.date_range("2026-01-01", periods=count, freq="1h", tz="UTC")
    close = [1.1 + index * 0.0001 for index in range(count)]
    strategy_features = pd.DataFrame({
        "date": dates,
        "%-momentum": [((index % 5) - 2) / 100 for index in range(count)],
        "&-direction": [("up", "down", "flat")[index % 3] for index in range(count)],
    })
    candles = pd.DataFrame({
        "date": dates,
        "open": close,
        "high": [value + 0.001 for value in close],
        "low": [value - 0.001 for value in close],
        "close": close,
        "volume": [100.0] * count,
    })
    dataset, manifest = build_forex_ai_dataset(
        candles,
        pair="EUR/USD",
        timeframe="H1",
        strategy_features=strategy_features,
        target_column="&-direction",
    )

    result = LightGBMDirectionClassifier(seed=7, estimators=10).fit_and_evaluate(
        dataset, manifest
    )

    assert set(result.class_labels) == {"up", "down", "flat"}
    assert set(dataset["label"]) == {"up", "down", "flat"}


def test_forex_ema_strategy_supports_freqai_predictions_and_threshold(tmp_path) -> None:
    from freqtrade.forex.cli import (
        _build_freqai_strategy_features,
        _run_lightgbm_hyperopt,
        _validate_freqai_strategy_consumption,
    )
    from freqtrade.forex.strategy_execution import CachedFreqAIPredictions

    freqai_config = {
        "enabled": True,
        "feature_parameters": {
            "include_timeframes": ["1h"],
            "include_corr_pairlist": [],
            "indicator_periods_candles": [3, 5],
            "include_shifted_candles": 1,
            "label_period_candles": 2,
            "principal_component_analysis": False,
        },
    }
    strategy = load_strategy(
        "ForexEmaStrategy", "1h", "EUR/USD", config_overrides={"freqai": freqai_config}
    )
    dates = pd.date_range("2026-01-01", periods=240, freq="1h", tz="UTC")
    close = [1.1 + index * 0.0001 + (index % 7) * 0.00003 for index in range(240)]
    candles = pd.DataFrame({
        "date": dates,
        "open": close,
        "high": [value + 0.001 for value in close],
        "low": [value - 0.001 for value in close],
        "close": close,
        "volume": [100.0 + index for index in range(len(close))],
    })

    feature_frame, target = _build_freqai_strategy_features(
        candles,
        strategy,
        pair="EUR/USD",
        timeframe="1h",
        freqai_config=freqai_config,
    )
    _validate_freqai_strategy_consumption(strategy, target)
    strategy.freqai = CachedFreqAIPredictions({
        int(timestamp.value): {"&-s_close": 0.002, "do_predict": 1}
        for timestamp in dates
    })
    prediction_frame = strategy.populate_indicators(candles.copy(), {"pair": "EUR/USD"})
    signals = strategy.populate_entry_trend(prediction_frame, {"pair": "EUR/USD"})

    assert target == "&-s_close"
    assert "%-return-3" in feature_frame.columns
    assert bool(signals.iloc[-1]["enter_long"])
    strategy.freqai_entry_threshold.value = 0.003
    higher_threshold_signals = strategy.populate_entry_trend(
        prediction_frame.copy(), {"pair": "EUR/USD"}
    )
    assert not bool(higher_threshold_signals.iloc[-1]["enter_long"])

    report_path, weights_path, report = _run_lightgbm_hyperopt(
        candles,
        instrument=OandaInstrument("EUR_USD", "EUR/USD", -4, 5, 0, Decimal("1")),
        pair="EUR/USD",
        timeframe="1h",
        model_name="LightGBMRegressor",
        epochs=2,
        model_dir=tmp_path,
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        slippage=Decimal("0"),
        financing_rate_per_day=Decimal("0"),
        quote_to_account_rate=Decimal("1"),
        stop_pips=Decimal("10"),
        hyperopt_loss="ProfitDrawDownHyperOptLoss",
        strategy_class="ForexEmaStrategy",
        freqai_config=freqai_config,
        informative_candles={
            "4h": pd.DataFrame({
                "date": pd.date_range(
                    "2025-12-31", periods=65, freq="4h", tz="UTC"
                ),
                "open": [1.1 + index * 0.0001 for index in range(65)],
                "high": [1.101 + index * 0.0001 for index in range(65)],
                "low": [1.099 + index * 0.0001 for index in range(65)],
                "close": [1.1 + index * 0.0001 for index in range(65)],
                "volume": [100.0] * 65,
            })
        },
    )
    assert report_path.is_file()
    assert weights_path.is_file()
    assert report["strategy"] == "ForexEmaStrategy"
    assert "freqai_entry_threshold" in report["strategy_parameters"]
    assert len(report["candidates"]) == 2


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