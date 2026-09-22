"""The cadence vocabulary is closed and model-authored, so every rejection
matters as much as every acceptance."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from eve.routines import cadence


def test_every_hours_is_valid_within_its_bounds():
    assert cadence.validate({"every_hours": 6}) is None
    assert cadence.validate({"every_hours": 1}) is None
    assert cadence.validate({"every_hours": 168}) is None


def test_a_sub_hourly_cadence_is_refused():
    """A VOICE turn every minute is the runaway this floor exists to stop."""
    assert cadence.validate({"every_hours": 0}) is not None
    assert cadence.validate({"every_hours": -1}) is not None


def test_a_cadence_longer_than_a_week_is_refused():
    assert cadence.validate({"every_hours": 169}) is not None


def test_a_boolean_is_not_an_hour_count():
    """bool is an int subclass; True must not pass as 1."""
    assert cadence.validate({"every_hours": True}) is not None


def test_daily_at_is_valid():
    assert cadence.validate({"daily_at": "08:00"}) is None
    assert cadence.validate({"daily_at": "23:59"}) is None


def test_a_malformed_time_is_refused():
    assert cadence.validate({"daily_at": "8am"}) is not None
    assert cadence.validate({"daily_at": "25:00"}) is not None
    assert cadence.validate({"daily_at": ""}) is not None


def test_weekly_at_is_valid():
    assert cadence.validate({"weekly_at": {"day": "sunday", "time": "19:00"}}) is None


def test_an_unknown_weekday_is_refused():
    assert cadence.validate({"weekly_at": {"day": "caturday", "time": "19:00"}}) is not None


def test_an_unknown_shape_is_refused():
    assert cadence.validate({"cron": "* * * * *"}) is not None
    assert cadence.validate({}) is not None
    assert cadence.validate({"every_hours": 6, "daily_at": "08:00"}) is not None


def test_a_non_dict_is_refused():
    assert cadence.validate("daily") is not None
    assert cadence.validate(None) is not None


def test_every_hours_advances_by_that_many_hours():
    after = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

    result = cadence.next_after({"every_hours": 6}, "America/Vancouver", after)

    assert result == datetime(2026, 3, 1, 18, 0, tzinfo=UTC)


def test_daily_at_lands_on_the_next_local_occurrence():
    """08:00 in Vancouver on a PST date is 16:00 UTC."""
    after = datetime(2026, 1, 10, 20, 0, tzinfo=UTC)  # 12:00 local

    result = cadence.next_after({"daily_at": "08:00"}, "America/Vancouver", after)

    assert result == datetime(2026, 1, 11, 16, 0, tzinfo=UTC)


def test_daily_at_uses_today_when_the_time_has_not_passed():
    after = datetime(2026, 1, 10, 8, 0, tzinfo=UTC)  # 00:00 local

    result = cadence.next_after({"daily_at": "08:00"}, "America/Vancouver", after)

    assert result == datetime(2026, 1, 10, 16, 0, tzinfo=UTC)


def test_daily_at_holds_local_time_across_a_dst_change():
    """The whole reason timezone is a stored column: 08:00 stays 08:00
    locally, so the UTC instant moves by an hour across the transition.
    US DST began 2026-03-08."""
    before = cadence.next_after(
        {"daily_at": "08:00"}, "America/Vancouver", datetime(2026, 3, 6, 20, 0, tzinfo=UTC)
    )
    after = cadence.next_after(
        {"daily_at": "08:00"}, "America/Vancouver", datetime(2026, 3, 9, 20, 0, tzinfo=UTC)
    )

    assert before.hour == 16  # PST, UTC-8
    assert after.hour == 15   # PDT, UTC-7


def test_weekly_at_finds_the_next_matching_weekday():
    # 2026-01-10 is a Saturday. 12:00 local.
    after = datetime(2026, 1, 10, 20, 0, tzinfo=UTC)

    result = cadence.next_after(
        {"weekly_at": {"day": "sunday", "time": "19:00"}}, "America/Vancouver", after
    )

    # Sunday 2026-01-11 19:00 PST is Monday 03:00 UTC.
    assert result == datetime(2026, 1, 12, 3, 0, tzinfo=UTC)


def test_weekly_at_rolls_a_full_week_when_today_already_passed():
    # Sunday 2026-01-11, 20:00 local, past the 19:00 slot.
    after = datetime(2026, 1, 12, 4, 0, tzinfo=UTC)

    result = cadence.next_after(
        {"weekly_at": {"day": "sunday", "time": "19:00"}}, "America/Vancouver", after
    )

    assert result == datetime(2026, 1, 19, 3, 0, tzinfo=UTC)


def test_an_unknown_timezone_falls_back_to_utc_rather_than_raising():
    """Same posture as eve_ambient.gates._zone: a bad timezone must not stop
    a routine from ever firing."""
    result = cadence.next_after(
        {"daily_at": "08:00"}, "Mars/Olympus", datetime(2026, 1, 10, 20, 0, tzinfo=UTC)
    )

    assert result == datetime(2026, 1, 11, 8, 0, tzinfo=UTC)


def test_next_after_always_returns_a_time_strictly_later():
    """The claim in store.claim_due compares on this. A cadence that returned
    its own input would re-fire the same occurrence forever."""
    after = datetime(2026, 1, 10, 16, 0, tzinfo=UTC)  # exactly 08:00 local

    result = cadence.next_after({"daily_at": "08:00"}, "America/Vancouver", after)

    assert result > after


@pytest.mark.parametrize(
    "value,expected",
    [
        ({"every_hours": 1}, "every hour"),
        ({"every_hours": 6}, "every 6 hours"),
        ({"daily_at": "08:00"}, "every day at 08:00"),
        ({"weekly_at": {"day": "sunday", "time": "19:00"}}, "every Sunday at 19:00"),
    ],
)
def test_describe_reads_as_english(value, expected):
    assert cadence.describe(value) == expected
