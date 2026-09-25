"""Timeframe helpers shared by the native Forex path."""

import re
from datetime import UTC, datetime, timedelta

from freqtrade.util.datetime_helpers import dt_from_ts, dt_ts

_TIMEFRAME_UNITS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 24 * 60 * 60,
    "w": 7 * 24 * 60 * 60,
    "M": 30 * 24 * 60 * 60,
    "y": 365 * 24 * 60 * 60,
}


def timeframe_to_seconds(timeframe: str) -> int:
    match = re.fullmatch(r"(\d+)([smhdwMy])", timeframe)
    if match is None:
        raise ValueError(f"Invalid timeframe: {timeframe}")
    amount, unit = match.groups()
    return int(amount) * _TIMEFRAME_UNITS[unit]


def timeframe_to_minutes(timeframe: str) -> int:
    return timeframe_to_seconds(timeframe) // 60


def timeframe_to_msecs(timeframe: str) -> int:
    return timeframe_to_seconds(timeframe) * 1000


def timeframe_to_prev_date(timeframe: str, date: datetime | None = None) -> datetime:
    if date is None:
        date = datetime.now(UTC)
    timestamp = dt_ts(date)
    interval = timeframe_to_seconds(timeframe) * 1000
    return dt_from_ts((timestamp // interval) * interval // 1000)


def timeframe_to_next_date(timeframe: str, date: datetime | None = None) -> datetime:
    if date is None:
        date = datetime.now(UTC)
    timestamp = dt_ts(date)
    interval = timeframe_to_seconds(timeframe) * 1000
    rounded = ((timestamp + interval - 1) // interval) * interval
    return dt_from_ts(rounded // 1000)


def date_minus_candles(timeframe: str, candle_count: int, date: datetime | None = None) -> datetime:
    if date is None:
        date = datetime.now(UTC)
    return timeframe_to_prev_date(timeframe, date) - timedelta(
        minutes=timeframe_to_minutes(timeframe) * candle_count
    )