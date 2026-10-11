from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

import pandas as pd

from freqtrade.forex.models import OandaInstrument
from freqtrade.forex.strategy_execution import load_strategy
from freqtrade.forex.strategy_hyperopt import run_strategy_hyperopt
from freqtrade.strategy import stoploss_from_absolute


def test_gold_scalping_calculates_ema_atr_and_rsi_indicators() -> None:
    strategy = load_strategy("GoldScalpingStrategy", "15m", "XAU/USD")
    close = [100.0 + index * 0.1 for index in range(220)]
    dataframe = pd.DataFrame(
        {
            "open": close,
            "high": [price + 1.0 for price in close],
            "low": [price - 1.0 for price in close],
            "close": close,
        }
    )

    result = strategy.populate_indicators(dataframe, {"pair": "XAU/USD"})

    assert result["ema50"].notna().iloc[-1]
    assert result["ema200"].notna().iloc[-1]
    assert result["ema50"].iloc[-1] > result["ema200"].iloc[-1]
    assert result["atr"].iloc[-1] > 0.0
    assert result["rsi"].iloc[-1] == 100.0


def test_gold_scalping_exposes_entry_and_exit_parameters_to_hyperopt() -> None:
    strategy = load_strategy("GoldScalpingStrategy", "15m", "XAU/USD")
    parameter_names = (
        "ema_fast_period",
        "ema_slow_period",
        "rsi_period",
        "rsi_entry_low",
        "rsi_entry_high",
        "atr_period",
        "atr_stop_multiplier",
        "take_profit_distance",
    )

    assert strategy.ema_fast_period.value == 50
    assert strategy.ema_slow_period.value == 200
    assert strategy.rsi_period.value == 14
    assert strategy.rsi_entry_low.value == 45
    assert strategy.rsi_entry_high.value == 55
    assert strategy.atr_period.value == 14
    assert strategy.atr_stop_multiplier.value == 1.0
    assert strategy.take_profit_distance.value == 2.0
    assert all(getattr(strategy, name).optimize for name in parameter_names)
    assert strategy.rsi_entry_low.high < strategy.rsi_entry_high.low
    assert strategy.ema_fast_period.high < strategy.ema_slow_period.low
    strategy.ema_fast_period.value = 30
    strategy.ema_slow_period.value = 120
    strategy.rsi_period.value = 7
    strategy.atr_period.value = 10
    strategy.rsi_entry_low.value = 40
    strategy.rsi_entry_high.value = 60
    strategy.atr_stop_multiplier.value = 1.5
    strategy.take_profit_distance.value = 3.0
    close = [100.0 + index * 0.1 for index in range(220)]
    indicators = strategy.populate_indicators(
        pd.DataFrame(
            {
                "open": close,
                "high": [price + 1.0 for price in close],
                "low": [price - 1.0 for price in close],
                "close": close,
            }
        ),
        {"pair": "XAU/USD"},
    )
    assert indicators["ema50"].notna().iloc[29]
    assert pd.isna(indicators["ema200"].iloc[118])
    assert indicators["ema200"].notna().iloc[119]


def test_gold_scalping_entry_conditions_match_pine_rules() -> None:
    strategy = load_strategy("GoldScalpingStrategy", "15m", "XAU/USD")
    dataframe = pd.DataFrame(
        {
            "open": [10.0, 8.9, 9.0, 10.1],
            "close": [9.0, 10.2, 10.0, 8.9],
            "ema50": [10.0, 10.0, 9.0, 9.0],
            "ema200": [9.0, 9.0, 10.0, 10.0],
            "rsi": [50.0, 50.0, 50.0, 50.0],
        }
    )

    result = strategy.populate_entry_trend(dataframe, {"pair": "XAU/USD"})

    assert result["enter_long"].tolist() == [False, True, False, False]
    assert result["enter_short"].tolist() == [False, False, False, True]


def test_gold_scalping_exits_at_signal_candle_atr_stop_or_fixed_target() -> None:
    strategy = load_strategy("GoldScalpingStrategy", "15m", "XAU/USD")
    dataframe = pd.DataFrame(
        {
            "close": [100.0, 101.0, 100.0, 100.0],
            "atr": [1.0, 1.0, 1.0, 1.0],
            "high": [100.0, 102.0, 100.5, 101.0],
            "low": [100.0, 100.5, 98.9, 99.5],
            "enter_long": [True, False, False, False],
            "enter_short": [False, False, False, False],
        }
    )

    result = strategy.populate_exit_trend(dataframe, {"pair": "XAU/USD"})

    assert result["exit_long"].tolist() == [False, True, True, False]
    assert not result["exit_short"].any()


def test_gold_scalping_short_exits_at_signal_candle_atr_stop_or_fixed_target() -> None:
    strategy = load_strategy("GoldScalpingStrategy", "15m", "XAU/USD")
    dataframe = pd.DataFrame(
        {
            "close": [200.0, 199.0, 200.0],
            "atr": [2.0, 2.0, 2.0],
            "high": [200.0, 199.5, 202.1],
            "low": [200.0, 198.0, 199.5],
            "enter_long": [False, False, False],
            "enter_short": [True, False, False],
        }
    )

    result = strategy.populate_exit_trend(dataframe, {"pair": "XAU/USD"})

    assert result["exit_short"].tolist() == [False, True, True]


def test_gold_scalping_persists_signal_price_levels_when_entry_fills() -> None:
    strategy = load_strategy("GoldScalpingStrategy", "15m", "XAU/USD")
    signal_time = pd.Timestamp("2026-01-01T00:15:00Z")
    entry_time = pd.Timestamp("2026-01-01T00:30:00Z")
    strategy.dp = Mock()
    strategy.dp.get_analyzed_dataframe.return_value = (
        pd.DataFrame(
            {
                "date": [
                    pd.Timestamp("2026-01-01T00:00:00Z"),
                    signal_time,
                ],
                "close": [100.0, 101.0],
                "atr": [1.0, 1.5],
                "enter_long": [False, True],
            }
        ),
        None,
    )
    values: dict[str, float] = {}
    trade = Mock(
        entry_side="buy",
        is_short=False,
        leverage=1.0,
        set_custom_data=lambda key, value: values.__setitem__(key, value),
        get_custom_data=lambda key: values.get(key),
    )
    order = Mock(ft_order_side="buy")

    strategy.order_filled("XAU/USD", trade, order, entry_time.to_pydatetime())

    assert values == {
        "gold_scalping_take_profit": 103.0,
        "gold_scalping_stop_loss": 99.5,
    }
    assert strategy.custom_exit(
        "XAU/USD", trade, datetime(2026, 1, 1, 0, 45, tzinfo=UTC), 103.0, 0.02
    ) == "gold_take_profit"
    assert strategy.custom_stoploss(
        "XAU/USD", trade, datetime(2026, 1, 1, 0, 45, tzinfo=UTC), 101.0, 0.0, False
    ) == stoploss_from_absolute(99.5, 101.0, is_short=False, leverage=1.0)


def test_gold_scalping_short_fill_uses_pine_price_anchors() -> None:
    strategy = load_strategy("GoldScalpingStrategy", "15m", "XAU/USD")
    signal_time = pd.Timestamp("2026-01-01T00:15:00Z")
    entry_time = pd.Timestamp("2026-01-01T00:30:00Z")
    strategy.dp = Mock()
    strategy.dp.get_analyzed_dataframe.return_value = (
        pd.DataFrame(
            {
                "date": [
                    pd.Timestamp("2026-01-01T00:00:00Z"),
                    signal_time,
                ],
                "close": [100.0, 101.0],
                "atr": [1.0, 1.5],
                "enter_short": [False, True],
            }
        ),
        None,
    )
    values: dict[str, float] = {}
    trade = Mock(
        entry_side="sell",
        is_short=True,
        set_custom_data=lambda key, value: values.__setitem__(key, value),
        get_custom_data=lambda key: values.get(key),
    )
    order = Mock(ft_order_side="sell")

    strategy.order_filled("XAU/USD", trade, order, entry_time.to_pydatetime())

    assert values == {
        "gold_scalping_take_profit": 99.0,
        "gold_scalping_stop_loss": 102.5,
    }
    assert strategy.custom_exit(
        "XAU/USD",
        trade,
        datetime(2026, 1, 1, 0, 45, tzinfo=UTC),
        99.0,
        0.02,
    ) == "gold_take_profit"


def test_gold_scalping_applies_optimized_atr_stop_and_take_profit_levels() -> None:
    strategy = load_strategy(
        "GoldScalpingStrategy",
        "15m",
        "XAU/USD",
        parameter_values={"atr_stop_multiplier": 2.0, "take_profit_distance": 3.0},
    )
    signal_time = pd.Timestamp("2026-01-01T00:15:00Z")
    entry_time = pd.Timestamp("2026-01-01T00:30:00Z")
    strategy.dp = Mock()
    strategy.dp.get_analyzed_dataframe.return_value = (
        pd.DataFrame(
            {
                "date": [
                    pd.Timestamp("2026-01-01T00:00:00Z"),
                    signal_time,
                ],
                "close": [100.0, 101.0],
                "atr": [1.0, 1.5],
                "enter_long": [False, True],
            }
        ),
        None,
    )
    values: dict[str, float] = {}
    trade = Mock(
        entry_side="buy",
        is_short=False,
        set_custom_data=lambda key, value: values.__setitem__(key, value),
    )

    strategy.order_filled(
        "XAU/USD",
        trade,
        Mock(ft_order_side="buy"),
        entry_time.to_pydatetime(),
    )

    assert values == {
        "gold_scalping_take_profit": 104.0,
        "gold_scalping_stop_loss": 98.0,
    }


def test_gold_scalping_hyperopt_samples_all_declared_parameters() -> None:
    close = [2000.0 + (index % 7) * 0.2 for index in range(100)]
    candles = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=100, freq="15min", tz="UTC"),
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
        OandaInstrument("XAU_USD", "XAU/USD", -1, 2, 0, 1),
        pair="XAU/USD",
        strategy_class="GoldScalpingStrategy",
        timeframe="15m",
        starting_balance=Decimal(10000),
        risk_fraction=Decimal("0.01"),
        spread=Decimal("0.1"),
        max_attempts=2,
        hyperopt_loss="ProfitDrawDownHyperOptLoss",
    )

    expected_parameters = {
        "ema_fast_period",
        "ema_slow_period",
        "rsi_period",
        "rsi_entry_low",
        "rsi_entry_high",
        "atr_period",
        "atr_stop_multiplier",
        "take_profit_distance",
    }
    assert len(candidates) == 2
    assert all(set(candidate["parameters"]) == expected_parameters for candidate in candidates)
    for candidate in candidates:
        sampled = candidate["parameters"]
        assert sampled["ema_fast_period"] < sampled["ema_slow_period"]
        assert sampled["rsi_entry_low"] < sampled["rsi_entry_high"]
