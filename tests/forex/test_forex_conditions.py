from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from freqtrade.forex.cli import build_parser
from freqtrade.forex.conditions import (
    add_ema_trend_indicators,
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
        }
    )


def test_ema_condition_builds_only_the_fixed_chained_ema_columns() -> None:
    result = add_ema_trend_indicators(_informative_candles("up"), timeperiod=2)

    assert {f"ema_x_{index}" for index in range(2, 11)} <= set(result.columns)
    assert "count_hi" not in result
    assert "count_low" not in result


def test_ema_condition_gates_entries_using_only_completed_higher_timeframe_candles() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    base = pd.DataFrame(
        {
            "date": [start + timedelta(minutes=15 * index) for index in range(4 * 80)],
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


def test_ema_condition_allows_shorts_during_a_higher_timeframe_downtrend() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    base = pd.DataFrame(
        {
            "date": [start + timedelta(minutes=15 * index) for index in range(4 * 80)],
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


def test_strategy_adapter_applies_the_shared_condition_without_strategy_changes() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    base = pd.DataFrame(
        {"date": [start + timedelta(minutes=15 * index) for index in range(4 * 80)]}
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

    assert long_adapter.signal(base) is Signal.LONG
    assert short_adapter.signal(base) is Signal.SHORT


def test_ema_trend_condition_settings_are_normalized_and_validated() -> None:
    assert normalize_ema_trend_condition(
        {"enabled": True, "timeframe": "H4", "timeperiod": 2}
    ) == {"enabled": True, "timeframe": "4h", "timeperiod": 2}


def test_hyperopt_cli_accepts_ema_trend_timeperiod_two() -> None:
    args = build_parser().parse_args(["hyperopt", "--ema-trend-timeperiod", "2"])

    assert args.ema_trend_timeperiod == 2


def test_condition_timeframe_and_warmup_are_included_in_required_history() -> None:
    strategy = load_strategy("ForexMasterStrategy", "15m", "EUR/USD")
    condition = {"enabled": True, "timeframe": "1h", "timeperiod": 13}

    assert "1h" in strategy_required_informative_timeframes(strategy, "EUR/USD", condition)
    assert strategy_informative_candle_count(strategy, "1h", 40, condition) >= 103


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
