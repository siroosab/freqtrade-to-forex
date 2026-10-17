from datetime import UTC, datetime

from freqtrade.forex.market_hours import market_schedule


def test_fx_session_is_open_before_friday_close():
    status = market_schedule(datetime(2026, 10, 16, 20, 0, tzinfo=UTC))

    assert status["scheduledOpen"] is True
    assert status["nextTransition"] == "close"
    assert status["nextTransitionAt"] == "2026-10-16T21:00:00+00:00"


def test_fx_session_closes_on_friday_and_reopens_sunday():
    status = market_schedule(datetime(2026, 10, 16, 21, 30, tzinfo=UTC))

    assert status["scheduledOpen"] is False
    assert status["nextTransition"] == "open"
    assert status["nextTransitionAt"] == "2026-10-18T21:00:00+00:00"


def test_fx_schedule_uses_new_york_dst_for_sunday_open():
    status = market_schedule(datetime(2026, 3, 8, 20, 0, tzinfo=UTC))

    assert status["scheduledOpen"] is False
    assert status["nextTransitionAt"] == "2026-03-08T21:00:00+00:00"
