from datetime import UTC, datetime
from unittest.mock import Mock

import pandas as pd

from freqtrade.forex.strategy_execution import load_strategy
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
