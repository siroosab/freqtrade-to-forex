"""Weekly FX market schedule in New York time."""

from datetime import UTC, datetime, time, timedelta

import pytz


_NEW_YORK = pytz.timezone("America/New_York")
_MARKET_OPEN = time(17, 0)
_MARKET_CLOSE = time(17, 0)


def market_schedule(now: datetime | None = None) -> dict[str, str | bool]:
    """Return the next weekly FX open/close transition, including DST changes."""
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    local_now = current.astimezone(_NEW_YORK)
    weekday = local_now.weekday()
    local_time = local_now.timetz().replace(tzinfo=None)

    is_open = (
        (weekday == 6 and local_time >= _MARKET_OPEN)
        or weekday in (0, 1, 2, 3)
        or (weekday == 4 and local_time < _MARKET_CLOSE)
    )

    if is_open:
        days_to_friday = (4 - weekday) % 7
        transition_day = local_now.date() + timedelta(days=days_to_friday)
        transition_time = _MARKET_CLOSE
        transition = "close"
    else:
        days_to_sunday = (6 - weekday) % 7
        transition_day = local_now.date() + timedelta(days=days_to_sunday)
        transition_time = _MARKET_OPEN
        transition = "open"

    next_transition = _NEW_YORK.localize(
        datetime.combine(transition_day, transition_time),
        is_dst=None,
    )
    if next_transition <= local_now:
        next_transition = _NEW_YORK.normalize(next_transition + timedelta(days=7))

    return {
        "scheduledOpen": is_open,
        "nextTransition": transition,
        "nextTransitionAt": next_transition.astimezone(UTC).isoformat(),
        "timezone": "America/New_York",
        "schedule": "Sunday 17:00 - Friday 17:00 New York time",
    }
