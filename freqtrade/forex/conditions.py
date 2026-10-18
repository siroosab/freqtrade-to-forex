"""Reusable strategy-wide entry conditions for forex strategies."""

from __future__ import annotations

from numbers import Integral

import pandas as pd
import talib.abstract as ta


EMA_TREND_EMA_COUNT = 9
EMA_TREND_TIMEFRAMES = {"H1": "1h", "1H": "1h", "1h": "1h", "H4": "4h", "4H": "4h", "4h": "4h"}
DEFAULT_EMA_TREND_CONDITION: dict[str, object] = {
    "enabled": True,
    "timeframe": "1h",
    "timeperiod": 4,
}


def normalize_ema_trend_condition(settings: object | None) -> dict[str, object]:
    """Validate and normalize the optional EMA trend condition settings."""
    if settings is None:
        return dict(DEFAULT_EMA_TREND_CONDITION)
    if not isinstance(settings, dict):
        raise ValueError("emaTrendCondition must be an object")

    enabled = settings.get("enabled", DEFAULT_EMA_TREND_CONDITION["enabled"])
    if not isinstance(enabled, bool):
        raise ValueError("emaTrendCondition.enabled must be a boolean")

    timeframe_value = settings.get("timeframe", DEFAULT_EMA_TREND_CONDITION["timeframe"])
    if not isinstance(timeframe_value, str):
        raise ValueError("emaTrendCondition.timeframe must be H1 or H4")
    timeframe = EMA_TREND_TIMEFRAMES.get(timeframe_value)
    if timeframe is None:
        raise ValueError("emaTrendCondition.timeframe must be H1 or H4")

    timeperiod = settings.get("timeperiod", DEFAULT_EMA_TREND_CONDITION["timeperiod"])
    if (
        not isinstance(timeperiod, Integral)
        or isinstance(timeperiod, bool)
        or not 2 <= timeperiod <= 13
    ):
        raise ValueError("emaTrendCondition.timeperiod must be an integer from 2 to 13")

    return {
        "enabled": enabled,
        "timeframe": timeframe,
        "timeperiod": int(timeperiod),
    }


def normalize_approved_ema_trend_condition(settings: object | None) -> dict[str, object]:
    """Keep pre-condition approvals behaviorally unchanged until they are re-approved."""
    if settings is None:
        return {"enabled": False, "timeframe": "1h", "timeperiod": 4}
    return normalize_ema_trend_condition(settings)


def add_ema_trend_indicators(dataframe: pd.DataFrame, *, timeperiod: int) -> pd.DataFrame:
    """Add chained EMA columns from ``ema_x_2`` through ``ema_x_10``."""
    if (
        not isinstance(timeperiod, Integral)
        or isinstance(timeperiod, bool)
        or not 2 <= timeperiod <= 13
    ):
        raise ValueError("timeperiod must be an integer from 2 to 13")
    if "close" not in dataframe:
        raise ValueError("EMA trend candles must include a close column")

    dataframe["ema_x_2"] = ta.EMA(dataframe["close"], timeperiod=2)
    for index in range(1, EMA_TREND_EMA_COUNT):
        column = f"ema_x_{index + 2}"
        dataframe[column] = ta.EMA(
            dataframe[f"ema_x_{index + 1}"],
            timeperiod=int(timeperiod),
        )
    return dataframe


def apply_ema_trend_condition(
    dataframe: pd.DataFrame,
    informative_candles: pd.DataFrame,
    *,
    base_timeframe: str,
    condition_timeframe: str,
    timeperiod: int,
) -> pd.DataFrame:
    """Gate strategy entry signals using the last completed higher-timeframe EMA pair."""
    from freqtrade.strategy.strategy_helper import merge_informative_pair

    ema_frame = add_ema_trend_indicators(informative_candles.copy(), timeperiod=timeperiod)
    result = merge_informative_pair(
        dataframe,
        ema_frame.loc[:, ["date", "ema_x_9", "ema_x_10"]],
        base_timeframe,
        condition_timeframe,
        ffill=True,
    )
    suffix = condition_timeframe.lower()
    fast_ema = result[f"ema_x_9_{suffix}"]
    slow_ema = result[f"ema_x_10_{suffix}"]
    if "enter_long" in result:
        result["enter_long"] = result["enter_long"].fillna(False).astype(bool) & fast_ema.gt(
            slow_ema
        )
    if "enter_short" in result:
        result["enter_short"] = result["enter_short"].fillna(False).astype(bool) & fast_ema.lt(
            slow_ema
        )
    return result
