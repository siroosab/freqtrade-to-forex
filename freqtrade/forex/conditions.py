"""Reusable strategy-wide entry conditions for forex strategies."""

from __future__ import annotations

from numbers import Integral

import pandas as pd
import talib.abstract as ta


EMA_TREND_TIMEPERIODS = (7, 9, 14, 19, 21, 37)
EMA_TREND_TIMEFRAMES = {
    "H1": "1h",
    "1H": "1h",
    "1h": "1h",
    "H4": "4h",
    "4H": "4h",
    "4h": "4h",
    "D1": "1d",
    "1D": "1d",
    "1d": "1d",
}
DEFAULT_EMA_TREND_CONDITION: dict[str, object] = {
    "enabled": True,
    "timeframe": "1h",
    "timeperiod": 7,
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
        raise ValueError("emaTrendCondition.timeframe must be H1, H4, or D1")
    timeframe = EMA_TREND_TIMEFRAMES.get(timeframe_value)
    if timeframe is None:
        raise ValueError("emaTrendCondition.timeframe must be H1, H4, or D1")

    timeperiod = settings.get("timeperiod", DEFAULT_EMA_TREND_CONDITION["timeperiod"])
    if (
        not isinstance(timeperiod, Integral)
        or isinstance(timeperiod, bool)
        or not 2 <= timeperiod <= 37
    ):
        raise ValueError("emaTrendCondition.timeperiod must be an integer from 2 to 37")

    return {
        "enabled": enabled,
        "timeframe": timeframe,
        "timeperiod": int(timeperiod),
    }


def normalize_approved_ema_trend_condition(settings: object | None) -> dict[str, object]:
    """Keep pre-condition approvals behaviorally unchanged until they are re-approved."""
    if settings is None:
        return {"enabled": False, "timeframe": "1h", "timeperiod": 7}
    return normalize_ema_trend_condition(settings)


def apply_ema_trend_condition(
    dataframe: pd.DataFrame,
    informative_candles: pd.DataFrame,
    *,
    base_timeframe: str,
    condition_timeframe: str,
    timeperiod: int,
) -> pd.DataFrame:
    """Gate entries using completed higher-timeframe EMA bands."""
    from freqtrade.strategy.strategy_helper import merge_informative_pair

    required_base = {"date", "high", "low"}
    missing_base = required_base - set(dataframe.columns)
    if missing_base:
        raise ValueError(
            "EMA trend candles must include date, high, and low columns"
        )
    required_informative = {"date", "high", "low"}
    missing_informative = required_informative - set(informative_candles.columns)
    if missing_informative:
        raise ValueError(
            "EMA trend informative candles must include date, high, and low columns"
        )
    if (
        not isinstance(timeperiod, Integral)
        or isinstance(timeperiod, bool)
        or not 2 <= timeperiod <= 37
    ):
        raise ValueError("timeperiod must be an integer from 2 to 37")

    ema_frame = informative_candles.loc[:, ["date", "high", "low"]].copy()
    ema_frame["ema_high"] = ta.EMA(ema_frame["high"], timeperiod=int(timeperiod))
    ema_frame["ema_low"] = ta.EMA(ema_frame["low"], timeperiod=int(timeperiod))
    result = merge_informative_pair(
        dataframe.copy(),
        ema_frame.loc[:, ["date", "ema_high", "ema_low"]],
        base_timeframe,
        condition_timeframe,
        ffill=True,
    )
    suffix = condition_timeframe.lower()
    higher_timeframe_ema_high = result[f"ema_high_{suffix}"]
    higher_timeframe_ema_low = result[f"ema_low_{suffix}"]
    if "enter_long" in result:
        result["enter_long"] = result["enter_long"].fillna(False).astype(bool) & (
            result["low"] > higher_timeframe_ema_high
        )
    if "enter_short" in result:
        result["enter_short"] = result["enter_short"].fillna(False).astype(bool) & (
            result["high"] < higher_timeframe_ema_low
        )
    return result
