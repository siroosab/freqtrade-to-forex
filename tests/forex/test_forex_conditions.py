from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from freqtrade.forex.cli import build_parser
from freqtrade.forex.conditions import (
    apply_ema_trend_condition,
    normalize_ema_trend_condition,
)
from freqtrade.forex.strategy_execution import (
    FreqtradeStrategyAdapter,
    load_strategy,
    strategy_informative_candle_count,
    strategy_required_informative_timeframes,
)
from freqtrade.forex.strategy_loop import Signal


class AlwaysSignalStrategy:
    timeframe = "15m"
    _ft_informative: tuple = ()
    minimal_roi: dict[str, float] = {}
    use_exit_signal = True

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["enter_long"] = True
        dataframe["enter_short"] = True
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["exit_long"] = False
        dataframe["exit_short"] = False
        return dataframe


def _informative_candles(direction: str) -> pd.DataFrame:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    closes = range(1, 101) if direction == "up" else range(101, 1, -1)
    return pd.DataFrame(
        {
            "date": [start + timedelta(hours=index) for index in range(100)],
            "close": list(closes),
            "high": [value + 0.5 for value in closes],
            "low": [value - 0.5 for value in closes],
        }
    )


def test_ema_condition_gates_entries_using_only_completed_higher_timeframe_candles() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    base = pd.DataFrame(
        {
            "date": [start + timedelta(minutes=15 * index) for index in range(4 * 80)],
            "close": [100.0] * (4 * 80),
            "high": [101.0] * (4 * 80),
            "low": [99.0] * (4 * 80),
            "enter_long": True,
            "enter_short": True,
        }
    )
    informative = _informative_candles("up")

    result = apply_ema_trend_condition(
        base,
        informative,
        base_timeframe="15m",
        condition_timeframe="1h",
        timeperiod=4,
    )

    assert not result.loc[0, "enter_long"]
    assert not result.loc[0, "enter_short"]
    assert result.loc[4 * 40, "enter_long"]
    assert not result.loc[4 * 40, "enter_short"]
    assert "ema_high_1h" in result
    assert "ema_low_1h" in result
    assert "ema_x_9_1h" not in result


def test_ema_condition_allows_shorts_during_a_higher_timeframe_downtrend() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    base = pd.DataFrame(
        {
            "date": [start + timedelta(minutes=15 * index) for index in range(4 * 80)],
            "close": [1.0] * (4 * 80),
            "high": [1.1] * (4 * 80),
            "low": [0.9] * (4 * 80),
            "enter_long": True,
            "enter_short": True,
        }
    )

    result = apply_ema_trend_condition(
        base,
        _informative_candles("down"),
        base_timeframe="15m",
        condition_timeframe="1h",
        timeperiod=4,
    )

    assert not result.loc[4 * 40, "enter_long"]
    assert result.loc[4 * 40, "enter_short"]


def test_ema_condition_long_requires_base_low_above_higher_timeframe_ema_of_high() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    base = pd.DataFrame(
        {
            "date": [start + timedelta(minutes=15 * index) for index in range(4 * 80)],
            "close": [100.0] * (4 * 80),
            "high": [101.0] * (4 * 80),
            "low": [100.0] * (4 * 80),
            "enter_long": True,
            "enter_short": True,
        }
    )

    result = apply_ema_trend_condition(
        base,
        _informative_candles("down"),
        base_timeframe="15m",
        condition_timeframe="1h",
        timeperiod=4,
    )

    assert result.loc[4 * 40, "low"] > result.loc[4 * 40, "ema_high_1h"]
    assert result.loc[4 * 40, "enter_long"]
    assert not result.loc[4 * 40, "enter_short"]


def test_ema_condition_short_requires_base_high_below_higher_timeframe_ema_of_low() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    base = pd.DataFrame(
        {
            "date": [start + timedelta(minutes=15 * index) for index in range(4 * 80)],
            "close": [1.0] * (4 * 80),
            "high": [1.0] * (4 * 80),
            "low": [0.9] * (4 * 80),
            "enter_long": True,
            "enter_short": True,
        }
    )

    result = apply_ema_trend_condition(
        base,
        _informative_candles("up"),
        base_timeframe="15m",
        condition_timeframe="1h",
        timeperiod=4,
    )

    assert result.loc[4 * 40, "high"] < result.loc[4 * 40, "ema_low_1h"]
    assert not result.loc[4 * 40, "enter_long"]
    assert result.loc[4 * 40, "enter_short"]


def test_ema_condition_does_not_use_an_incomplete_or_future_higher_timeframe_candle() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    base = pd.DataFrame(
        {
            "date": [start + timedelta(minutes=15 * index) for index in range(8)],
            "high": [200.0] * 8,
            "low": [100.0] * 8,
            "enter_long": True,
            "enter_short": True,
        }
    )
    informative = pd.DataFrame(
        {
            "date": [start, start + timedelta(hours=1)],
            "high": [10.0, 1000.0],
            "low": [5.0, 900.0],
        }
    )

    result = apply_ema_trend_condition(
        base,
        informative,
        base_timeframe="15m",
        condition_timeframe="1h",
        timeperiod=2,
    )

    assert pd.isna(result.loc[2, "ema_high_1h"])
    assert not bool(result.loc[2, "enter_long"])
    assert pd.isna(result.loc[3, "ema_high_1h"])
    assert pd.isna(result.loc[4, "ema_high_1h"])
    assert result.loc[7, "ema_high_1h"] > 10.0


def test_strategy_adapter_applies_the_shared_condition_without_strategy_changes() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    dates = [start + timedelta(minutes=15 * index) for index in range(4 * 80)]
    long_base = pd.DataFrame(
        {
            "date": dates,
            "close": [100.0] * (4 * 80),
            "high": [101.0] * (4 * 80),
            "low": [100.0] * (4 * 80),
        }
    )
    short_base = pd.DataFrame(
        {
            "date": dates,
            "close": [1.0] * (4 * 80),
            "high": [1.0] * (4 * 80),
            "low": [0.9] * (4 * 80),
        }
    )
    settings = {"enabled": True, "timeframe": "1h", "timeperiod": 4}

    long_adapter = FreqtradeStrategyAdapter(
        AlwaysSignalStrategy(),
        "EUR/USD",
        {"1h": _informative_candles("up")},
        ema_trend_condition=settings,
    )
    short_adapter = FreqtradeStrategyAdapter(
        AlwaysSignalStrategy(),
        "EUR/USD",
        {"1h": _informative_candles("down")},
        ema_trend_condition=settings,
    )

    assert long_adapter.signal(long_base) is Signal.LONG
    assert short_adapter.signal(short_base) is Signal.SHORT


def test_ema_trend_condition_settings_are_normalized_and_validated() -> None:
    assert normalize_ema_trend_condition(
        {"enabled": True, "timeframe": "D1", "timeperiod": 37}
    ) == {"enabled": True, "timeframe": "1d", "timeperiod": 37}
    assert normalize_ema_trend_condition(None) == {
        "enabled": True,
        "timeframe": "1h",
        "timeperiod": 7,
    }


def test_hyperopt_cli_accepts_ema_trend_timeperiod_seven_and_daily_timeframe() -> None:
    args = build_parser().parse_args(
        ["hyperopt", "--ema-trend-timeperiod", "7", "--ema-trend-timeframe", "D1"]
    )

    assert args.ema_trend_timeperiod == 7
    assert args.ema_trend_timeframe == "D1"


def test_condition_timeframe_and_warmup_are_included_in_required_history() -> None:
    strategy = load_strategy("ForexMasterStrategy", "15m", "EUR/USD")
    condition = {"enabled": True, "timeframe": "1h", "timeperiod": 37}

    assert "1h" in strategy_required_informative_timeframes(strategy, "EUR/USD", condition)
    assert strategy_informative_candle_count(strategy, "1h", 40, condition) >= 42
    assert "1d" in strategy_required_informative_timeframes(
        strategy,
        "EUR/USD",
        {"enabled": True, "timeframe": "1d", "timeperiod": 7},
    )


@pytest.mark.parametrize("condition_timeframe", ["1h", "4h"])
def test_condition_rejects_a_timeframe_not_higher_than_strategy_timeframe(
    condition_timeframe: str,
) -> None:
    strategy = load_strategy("ForexMasterStrategy", "4h", "EUR/USD")

    with pytest.raises(ValueError, match="higher than the strategy timeframe"):
        strategy_required_informative_timeframes(
            strategy,
            "EUR/USD",
            {"enabled": True, "timeframe": condition_timeframe, "timeperiod": 4},
        )
