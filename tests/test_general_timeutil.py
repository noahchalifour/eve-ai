"""`date_time` (ENG-372): resolved against the member's own clock."""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from eve.general import timeutil
from eve.general.timeutil import TimeError, parse_moment, run

# Los Angeles, not Vancouver: this tzdata has British Columbia on permanent
# UTC-7 from November 2026, which would make the DST test meaningless.
TZ = ZoneInfo("America/Los_Angeles")
# Wednesday 2026-10-07 14:00 in Los Angeles (PDT, UTC-7).
NOW = datetime(2026, 10, 7, 21, 0, tzinfo=UTC)


def _p(text):
    return parse_moment(text, TZ, NOW)


def test_relative_phrases_resolve_in_local_time():
    assert _p("in 20 minutes") == datetime(2026, 10, 7, 14, 20, tzinfo=TZ)
    assert _p("tomorrow at 9am") == datetime(2026, 10, 8, 9, 0, tzinfo=TZ)
    assert _p("friday 17:30") == datetime(2026, 10, 9, 17, 30, tzinfo=TZ)
    assert _p("next wednesday") == datetime(2026, 10, 14, 0, 0, tzinfo=TZ)
    assert _p("wednesday") == datetime(2026, 10, 7, 0, 0, tzinfo=TZ)
    assert _p("last monday") == datetime(2026, 10, 5, 0, 0, tzinfo=TZ)
    assert _p("christmas") == datetime(2026, 12, 25, 0, 0, tzinfo=TZ)
    assert _p("6 weeks from friday") == datetime(2026, 11, 20, 0, 0, tzinfo=TZ)
    assert _p("in 1h30m") == datetime(2026, 10, 7, 15, 30, tzinfo=TZ)


def test_a_bare_clock_time_already_past_means_tomorrow():
    assert _p("3pm") == datetime(2026, 10, 7, 15, 0, tzinfo=TZ)
    assert _p("9am") == datetime(2026, 10, 8, 9, 0, tzinfo=TZ)


def test_iso_with_and_without_offset():
    assert _p("2026-12-25T15:00") == datetime(2026, 12, 25, 15, 0, tzinfo=TZ)
    assert _p("2026-12-25T15:00:00+00:00") == datetime(2026, 12, 25, 7, 0, tzinfo=TZ)


def test_unreadable_input_is_an_error_not_a_guess():
    for text in ("sometime soon", "3", "in a while", ""):
        with pytest.raises(TimeError):
            _p(text)


def test_convert_across_zones():
    out = run("convert", "America/Toronto", NOW, time_text="3pm", timezone="Pacific", to_timezone="Tokyo")
    assert "07:00 JST" in out  # 3pm PDT Wed -> 7am JST Thu
    assert "Thursday" in out


def test_now_in_another_zone():
    out = run("now", "America/Los_Angeles", NOW, timezone="Europe/Paris")
    assert "23:00 CEST" in out


def test_diff_counts_calendar_days_until_christmas():
    out = run("diff", "America/Los_Angeles", NOW, end="christmas")
    assert "79 calendar days" in out


def test_diff_across_the_dst_change_is_wall_clock_correct():
    # Los Angeles falls back on 2026-11-01: 24 hours of wall clock is 25 elapsed.
    out = run("diff", "America/Los_Angeles", NOW, start="2026-10-31T12:00", end="2026-11-01T12:00")
    assert "1 day, 1 hour" in out
    assert "1 calendar day" in out


def test_add_months_clamps_to_month_end():
    out = run("add", "UTC", NOW, start="2026-01-31", duration="1 month")
    assert "2026-02-28" in out
    out = run("add", "UTC", NOW, start="2026-03-10", duration="-3 days")
    assert "2026-03-07" in out


def test_unknown_operation_and_zone():
    with pytest.raises(TimeError, match="operation"):
        run("tomorrow", "UTC", NOW)
    with pytest.raises(TimeError, match="timezone"):
        run("now", "UTC", NOW, timezone="Atlantis")


def test_the_tool_uses_the_members_timezone(monkeypatch):
    config = {"configurable": {"member": {"timezone": "Asia/Tokyo"}}}
    out = timeutil.date_time.invoke({"operation": "now"}, config)
    assert "JST" in out
    assert timeutil.date_time.invoke({"operation": "bogus"}, config).startswith("error:")


def test_hours_are_elapsed_but_days_keep_the_clock_across_dst():
    before = datetime(2026, 10, 31, 22, 0, tzinfo=TZ)
    # 6 real hours after 22:00 PDT on the fall-back night is 03:00 PST.
    assert timeutil.shift(before, timeutil.timedelta(hours=6), 0) == datetime(2026, 11, 1, 3, 0, tzinfo=TZ)
    # One day later is 22:00 again, wall clock.
    assert timeutil.shift(before, timeutil.timedelta(days=1), 0) == datetime(2026, 11, 1, 22, 0, tzinfo=TZ)
