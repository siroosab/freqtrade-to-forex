from decimal import Decimal

import pandas as pd
import pytest

from freqtrade.forex.ai_hyperopt import (
    _estimate_roi_volatility_per_5m,
    _generate_roi_table,
    _roi_profit_bounds,
    _roi_space,
    _sample_roi_parameters,
    run_strategy_hyperopt,
)
from freqtrade.forex.models import OandaInstrument
from freqtrade.forex.strategy_execution import (
    FreqtradeStrategyAdapter,
    freqtrade_timeframe,
    load_strategy,
    strategy_informative_timeframes,
)
from freqtrade.strategy.parameters import IntParameter


def test_roi_hyperopt_ranges_adapt_to_low_medium_and_high_volatility() -> None:
    def candles_with_range(range_rate: float) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "high": [1.0 + range_rate / 2] * 80,
                "low": [1.0 - range_rate / 2] * 80,
                "close": [1.0] * 80,
            }
        )

    low_volatility = _estimate_roi_volatility_per_5m(
        candles_with_range(0.00005), "5m"
    )
    medium_volatility = _estimate_roi_volatility_per_5m(
        candles_with_range(0.0002), "5m"
    )
    high_volatility = _estimate_roi_volatility_per_5m(
        candles_with_range(0.0008), "5m"
    )

    low_space = _roi_space("5m", low_volatility)
    medium_space = _roi_space("5m", medium_volatility)
    high_space = _roi_space("5m", high_volatility)

    assert _estimate_roi_volatility_per_5m(
        candles_with_range(0.0002 * (12**0.5)), "1h"
    ) == pytest.approx(medium_volatility)
    assert low_space["roi_t1"][0] > medium_space["roi_t1"][0]
    assert medium_space["roi_t1"][0] > high_space["roi_t1"][0]
    assert low_space["roi_p1"][1] < medium_space["roi_p1"][1]
    assert medium_space["roi_p1"][1] < high_space["roi_p1"][1]


def test_sampled_roi_profit_steps_fit_volatility_and_keep_sub_percent_precision() -> None:
    import random

    volatility = 0.00005
    space = _roi_space("5m", volatility)
    sampled = _sample_roi_parameters(random.Random(42), "5m", volatility)

    for name in ("roi_t1", "roi_t2", "roi_t3"):
        assert space[name][0] <= sampled[name] <= space[name][1]
    for profit_name, time_name in (
        ("roi_p1", "roi_t1"),
        ("roi_p2", "roi_t2"),
        ("roi_p3", "roi_t3"),
    ):
        low, high = _roi_profit_bounds(
            sampled[time_name], volatility
        )
        assert low <= sampled[profit_name] <= high
    assert sampled["roi_p1"] < 0.001
    roi_table = _generate_roi_table(sampled)
    assert 0 < roi_table["0"] < 0.003
    assert any(rate != round(rate, 3) for rate in roi_table.values() if rate > 0)


def _entry_frame(strategy, count: int = 162) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close": [1.1] * count,
            "ema50": [1.0] * count,
            "rsi_ha_sma_rounded": [50.0] * count,
            "max_4h": [False] * count,
            "min_4h": [False] * count,
            "volume": [0.0] * count,
        }
    )


def test_forex_sample_strategy_loads_and_declares_valid_hyperopt_parameters() -> None:
    from freqtrade.forex import ForexSampleStrategy

    strategy = load_strategy("ForexSampleStrategy", "15m", "EUR/USD")

    assert ForexSampleStrategy.__name__ == strategy.__class__.__name__
    assert strategy.can_short
    assert strategy_informative_timeframes(strategy, "EUR/USD") == ("4h",)
    assert strategy.buy_rsi.low <= strategy.buy_rsi.value <= strategy.buy_rsi.high
    assert strategy.sell_rsi.low <= strategy.sell_rsi.value <= strategy.sell_rsi.high
    assert strategy.max_streak.value in strategy.max_streak.range
    assert strategy.min_streak.value in strategy.min_streak.range
    assert strategy.minimal_roi == {
        "0": 0.0005,
        "60": 0.0004,
        "180": 0.0003,
        "360": 0.0002,
        "720": 0.0,
    }
    assert not strategy.use_exit_signal


def test_approved_roi_schedule_overrides_strategy_default() -> None:
    optimized_roi = {"0": 0.012, "45": 0.004, "120": 0.0}

    strategy = load_strategy(
        "ForexSampleStrategy",
        "5m",
        "EUR/USD",
        minimal_roi=optimized_roi,
    )

    assert strategy.minimal_roi == optimized_roi


def test_freqtrade_timeframe_distinguishes_one_minute_from_one_month() -> None:
    assert freqtrade_timeframe("1m") == "1m"
    assert freqtrade_timeframe("M1") == "1m"
    assert freqtrade_timeframe("1M") == "1M"


def test_forex_sample_strategy_generates_long_and_short_trend_signals() -> None:
    strategy = load_strategy("ForexSampleStrategy", "15m", "EUR/USD")

    bullish = _entry_frame(strategy)
    bullish.loc[160, "max_4h"] = True
    bullish.loc[161, "max_4h"] = True
    bullish.loc[160, "rsi_ha_sma_rounded"] = strategy.buy_rsi.value - 1
    bullish.loc[161, "rsi_ha_sma_rounded"] = strategy.buy_rsi.value + 1
    long_entries = strategy.populate_entry_trend(bullish, {"pair": "EUR/USD"})
    assert bool(long_entries.iloc[-1]["enter_long"])
    assert not bool(long_entries.iloc[-1]["enter_short"])

    bearish = _entry_frame(strategy)
    bearish["close"] = 0.9
    bearish.loc[160, "min_4h"] = True
    bearish.loc[161, "min_4h"] = True
    bearish.loc[160, "rsi_ha_sma_rounded"] = strategy.sell_rsi.value + 1
    bearish.loc[161, "rsi_ha_sma_rounded"] = strategy.sell_rsi.value - 1
    short_entries = strategy.populate_entry_trend(bearish, {"pair": "EUR/USD"})
    assert bool(short_entries.iloc[-1]["enter_short"])
    assert not bool(short_entries.iloc[-1]["enter_long"])


def test_forex_sample_strategy_applies_freqai_prediction_to_base_signals() -> None:
    strategy = load_strategy("ForexSampleStrategy", "15m", "EUR/USD")
    dataframe = _entry_frame(strategy)
    dataframe.loc[161, "max_4h"] = True
    dataframe.loc[160, "rsi_ha_sma_rounded"] = strategy.buy_rsi.value - 1
    dataframe.loc[161, "rsi_ha_sma_rounded"] = strategy.buy_rsi.value + 1
    dataframe["&-s_close"] = 0.001
    dataframe["do_predict"] = 1

    long_entries = strategy.populate_entry_trend(dataframe, {"pair": "EUR/USD"})
    assert bool(long_entries.iloc[-1]["enter_long"])

    strategy.freqai_entry_threshold.value = 0.002
    filtered_prediction = strategy.populate_entry_trend(dataframe, {"pair": "EUR/USD"})
    assert not bool(filtered_prediction.iloc[-1]["enter_long"])

    strategy.freqai_entry_threshold.value = 0.0001
    dataframe["&-s_close"] = -0.001
    short_prediction = strategy.populate_entry_trend(dataframe, {"pair": "EUR/USD"})
    assert not bool(short_prediction.iloc[-1]["enter_long"])
    assert not bool(short_prediction.iloc[-1]["enter_short"])

    dataframe["&-s_close"] = "long"
    dataframe["freqai_confidence"] = 0.4
    strategy.freqai_confidence_threshold.value = 0.5
    low_confidence = strategy.populate_entry_trend(dataframe, {"pair": "EUR/USD"})
    assert not bool(low_confidence.iloc[-1]["enter_long"])
    strategy.freqai_confidence_threshold.value = 0.35
    accepted_confidence = strategy.populate_entry_trend(
        dataframe, {"pair": "EUR/USD"}
    )
    assert bool(accepted_confidence.iloc[-1]["enter_long"])
    assert not hasattr(strategy, "_get_pair_param")


def test_forex_sample_strategy_exposes_freqai_features_targets_and_start() -> None:
    freqai_config = {
        "enabled": True,
        "feature_parameters": {"label_period_candles": 2},
    }
    strategy = load_strategy(
        "ForexSampleStrategy",
        "15m",
        "EUR/USD",
        config_overrides={"freqai": freqai_config},
    )
    start_calls: list[str] = []

    class FakeFreqAI:
        def start(self, dataframe, metadata, selected_strategy):
            start_calls.append(selected_strategy.__class__.__name__)
            return dataframe

    strategy.freqai = FakeFreqAI()
    dates = pd.date_range("2026-01-01", periods=20, freq="15min", tz="UTC")
    candles = pd.DataFrame(
        {
            "date": dates,
            "open": [1.0 + index * 0.001 for index in range(len(dates))],
            "high": [1.01 + index * 0.001 for index in range(len(dates))],
            "low": [0.99 + index * 0.001 for index in range(len(dates))],
            "close": [1.005 + index * 0.001 for index in range(len(dates))],
        }
    )

    indicators = strategy.populate_indicators(candles.copy(), {"pair": "EUR/USD"})
    features = strategy.feature_engineering_expand_all(candles.copy(), 3, {"pair": "EUR/USD"})
    targets = strategy.set_freqai_targets(candles.copy(), {"pair": "EUR/USD"})

    assert start_calls == ["ForexSampleStrategy"]
    assert {"%-return-3", "%-range-3", "%-ema-spread-3"} <= set(features.columns)
    assert "%-session-hour" in strategy.feature_engineering_standard(
        candles.copy(), {"pair": "EUR/USD"}
    )
    assert targets["&-s_close"].iloc[0] == pytest.approx(
        candles["close"].iloc[2] / candles["close"].iloc[0] - 1.0
    )
    assert "ema50" in indicators


def test_forex_sample_strategy_builds_only_required_indicators() -> None:
    strategy = load_strategy("ForexSampleStrategy", "15m", "EUR/USD")
    candles = pd.DataFrame(
        {
            "open": [1.0 + index * 0.001 for index in range(80)],
            "high": [1.01 + index * 0.001 for index in range(80)],
            "low": [0.99 + index * 0.001 for index in range(80)],
            "close": [1.005 + index * 0.001 for index in range(80)],
        }
    )
    indicators = strategy.populate_indicators(candles, {"pair": "EUR/USD"})
    higher_timeframe = strategy.populate_indicators_4h(
        candles.assign(date=pd.date_range("2026-01-01", periods=80, freq="4h")),
        {"pair": "EUR/USD"},
    )

    assert {"ha_close", "rsi_ha", "rsi_sma_ha", "rsi_ha_sma_rounded", "ema50"} <= set(
        indicators.columns
    )
    assert {"max", "min"} <= set(higher_timeframe.columns)
    assert higher_timeframe["max"].dtype == bool
    assert higher_timeframe["min"].dtype == bool


def test_forex_sample_strategy_integrates_with_informative_backtest_and_hyperopt() -> None:
    dates = pd.date_range("2026-01-01", periods=80, freq="5min", tz="UTC")
    closes = [1.1 + index * 0.00001 for index in range(len(dates))]
    candles = pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [value + 0.001 for value in closes],
            "low": [value - 0.001 for value in closes],
            "close": closes,
            "volume": [0.0] * len(dates),
        }
    )
    informative_dates = pd.date_range("2025-12-20", periods=100, freq="4h", tz="UTC")
    informative_closes = [1.0 + index * 0.0001 for index in range(len(informative_dates))]
    informative = pd.DataFrame(
        {
            "date": informative_dates,
            "open": informative_closes,
            "high": [value + 0.001 for value in informative_closes],
            "low": [value - 0.001 for value in informative_closes],
            "close": informative_closes,
            "volume": [0.0] * len(informative_dates),
        }
    )

    strategy = load_strategy("ForexSampleStrategy", "5m", "EUR/USD")
    adapter = FreqtradeStrategyAdapter(strategy, "EUR/USD", {"4h": informative})
    populated = adapter._populate(candles)
    assert {"max_4h", "min_4h", "max_or_min"} <= set(populated.columns)

    candidates = run_strategy_hyperopt(
        candles,
        {"4h": informative},
        OandaInstrument("EUR_USD", "EUR/USD", -4, 5, 0, Decimal(1)),
        pair="EUR/USD",
        strategy_class="ForexSampleStrategy",
        timeframe="5m",
        starting_balance=Decimal(10000),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        max_attempts=1,
        hyperopt_loss="ProfitDrawDownHyperOptLoss",
    )
    assert len(candidates) == 1
    assert set(candidates[0]["parameters"]) == {
        "buy_rsi",
        "sell_rsi",
        "max_streak",
        "min_streak",
    }
    assert set(candidates[0]["roi_parameters"]) == {
        "roi_t1",
        "roi_t2",
        "roi_t3",
        "roi_p1",
        "roi_p2",
        "roi_p3",
    }
    assert len(candidates[0]["minimal_roi"]) == 4

    freqai_config = {
        "enabled": True,
        "feature_parameters": {"label_period_candles": 2},
    }
    regression_predictions = {
        int(pd.Timestamp(date).value): {
            "&-s_close": 0.001,
            "do_predict": 1,
        }
        for date in dates
    }
    regression_candidates = run_strategy_hyperopt(
        candles,
        {"4h": informative},
        OandaInstrument("EUR_USD", "EUR/USD", -4, 5, 0, Decimal(1)),
        pair="EUR/USD",
        strategy_class="ForexSampleStrategy",
        timeframe="5m",
        starting_balance=Decimal(10000),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        max_attempts=1,
        hyperopt_loss="ProfitDrawDownHyperOptLoss",
        freqai_config=freqai_config,
        freqai_predictions=regression_predictions,
        freqai_target_column="&-s_close",
    )
    assert set(regression_candidates[0]["parameters"]) == {
        "buy_rsi",
        "sell_rsi",
        "max_streak",
        "min_streak",
        "freqai_entry_threshold",
    }

    classifier_predictions = {
        int(pd.Timestamp(date).value): {
            "&-s_close": "long",
            "freqai_confidence": 0.9,
            "do_predict": 1,
        }
        for date in dates
    }
    classifier_candidates = run_strategy_hyperopt(
        candles,
        {"4h": informative},
        OandaInstrument("EUR_USD", "EUR/USD", -4, 5, 0, Decimal(1)),
        pair="EUR/USD",
        strategy_class="ForexSampleStrategy",
        timeframe="5m",
        starting_balance=Decimal(10000),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.0001"),
        max_attempts=1,
        hyperopt_loss="ProfitDrawDownHyperOptLoss",
        freqai_config=freqai_config,
        freqai_predictions=classifier_predictions,
        freqai_target_column="&-s_close",
    )
    assert set(classifier_candidates[0]["parameters"]) == {
        "buy_rsi",
        "sell_rsi",
        "max_streak",
        "min_streak",
        "freqai_confidence_threshold",
    }


def test_strategy_hyperopt_optimizes_roi_on_five_minute_data_without_exit_params(
    monkeypatch,
) -> None:
    class RoiOnlyOptimizationStrategy:
        timeframe = "5m"
        minimal_roi = {"0": 0.0005}
        use_exit_signal = False
        _ft_informative = ()
        exit_level = IntParameter(1, 2, default=1, space="sell")

        def __init__(self) -> None:
            self.exit_level = IntParameter(1, 2, default=1, space="sell")

        @staticmethod
        def populate_indicators(dataframe, metadata):
            return dataframe

        @staticmethod
        def populate_entry_trend(dataframe, metadata):
            result = dataframe.copy()
            result["enter_long"] = [
                index % 20 == 0 for index in range(len(result))
            ]
            result["enter_short"] = False
            return result

        @staticmethod
        def populate_exit_trend(dataframe, metadata):
            result = dataframe.copy()
            result["exit_long"] = False
            result["exit_short"] = False
            return result

    monkeypatch.setattr(
        "freqtrade.forex.ai_hyperopt.load_strategy",
        lambda *args, **kwargs: RoiOnlyOptimizationStrategy(),
    )
    dates = pd.date_range("2026-01-01", periods=80, freq="5min", tz="UTC")
    candles = pd.DataFrame(
        {
            "date": dates,
            "open": [1.0] * len(dates),
            "high": [1.5] * len(dates),
            "low": [1.0] * len(dates),
            "close": [1.0] * len(dates),
            "volume": [0.0] * len(dates),
        }
    )

    candidates = run_strategy_hyperopt(
        candles,
        {},
        OandaInstrument("EUR_USD", "EUR/USD", -4, 5, 0, Decimal(1)),
        pair="EUR/USD",
        strategy_class="RoiOnlyOptimizationStrategy",
        timeframe="5m",
        starting_balance=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0"),
        stop_pips=Decimal("10"),
        max_attempts=1,
        hyperopt_loss="ProfitDrawDownHyperOptLoss",
    )

    candidate = candidates[0]
    assert candidate["parameters"] == {}
    assert candidate["validationTrades"] > 0
    assert candidate["roi_volatility_regime"] == "high"
    assert candidate["roi_volatility_per_5m"] == pytest.approx(0.002)
    assert set(candidate["roi_parameters"]) == {
        "roi_t1",
        "roi_t2",
        "roi_t3",
        "roi_p1",
        "roi_p2",
        "roi_p3",
    }
    roi_parameters = candidate["roi_parameters"]
    roi_space = _roi_space("5m", candidate["roi_volatility_per_5m"])
    assert roi_space["roi_t1"][0] <= roi_parameters["roi_t1"] <= roi_space["roi_t1"][1]
    assert roi_space["roi_t2"][0] <= roi_parameters["roi_t2"] <= roi_space["roi_t2"][1]
    assert roi_space["roi_t3"][0] <= roi_parameters["roi_t3"] <= roi_space["roi_t3"][1]
    for profit_name, time_name in (
        ("roi_p1", "roi_t1"),
        ("roi_p2", "roi_t2"),
        ("roi_p3", "roi_t3"),
    ):
        low, high = _roi_profit_bounds(
            roi_parameters[time_name], candidate["roi_volatility_per_5m"]
        )
        assert low <= roi_parameters[profit_name] <= high
    roi_steps = sorted(
        (int(minutes), float(rate))
        for minutes, rate in candidate["minimal_roi"].items()
    )
    assert [minutes for minutes, _ in roi_steps] == sorted(
        {minutes for minutes, _ in roi_steps}
    )
    assert [rate for _, rate in roi_steps] == sorted(
        (rate for _, rate in roi_steps), reverse=True
    )
    assert roi_steps[-1][1] == 0
