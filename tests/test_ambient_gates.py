from datetime import UTC, datetime, time

import pytest

from eve_ambient import gates
from eve_ambient.types import Signal

ROSTER = """
members:
  - sub: "sub-noah"
    name: "Noah"
    role: adult
    timezone: "America/Vancouver"
    permissions: [mail.read, finances, home.control, calendar.read, computer.use]
  - sub: "sub-kid"
    name: "Kid"
    role: child
    timezone: "America/Toronto"
    permissions: [home.control]
"""


@pytest.fixture(autouse=True)
def roster(tmp_path, monkeypatch):
    path = tmp_path / "family.yaml"
    path.write_text(ROSTER)
    monkeypatch.setenv("EVE_FAMILY_FILE", str(path))
    from eve.family import get_family
    from eve.settings import get_settings

    get_settings.cache_clear()
    get_family.cache_clear()
    yield
    get_settings.cache_clear()
    get_family.cache_clear()


def _signal(source: str, member_sub: str | None = None) -> Signal:
    return Signal(
        source=source, key="k", occurred_at=datetime.now(UTC),
        member_sub=member_sub, summary="s", payload={},
    )


def test_mail_may_only_notify_its_own_owner():
    """Private correspondence is not the filter's to redistribute, and no
    permission string expresses "may read Noah's mail"."""
    audience = gates.scoped_audience(_signal("mail", "sub-noah"), ["sub-noah", "sub-kid"])
    assert audience == ["sub-noah"]


def test_a_calendar_signal_may_notify_someone_else():
    """A family calendar is shared logistics: a kid's game on one calendar is
    news for the parent doing the driving."""
    audience = gates.scoped_audience(_signal("calendar", "sub-noah"), ["sub-kid"])
    assert audience == ["sub-kid"]


def test_a_household_signal_keeps_the_filters_whole_audience():
    audience = gates.scoped_audience(_signal("finances", None), ["sub-noah", "sub-kid"])
    assert audience == ["sub-noah", "sub-kid"]


def test_a_member_lacking_the_permission_is_dropped():
    assert gates.permitted(_signal("finances"), ["sub-noah", "sub-kid"]) == ["sub-noah"]


def test_a_member_holding_the_permission_is_kept():
    assert gates.permitted(_signal("home"), ["sub-kid"]) == ["sub-kid"]


def test_an_unknown_subject_is_dropped_rather_than_raising():
    """The filter names subs; a hallucinated one must not kill the tick."""
    assert gates.permitted(_signal("home"), ["sub-nobody"]) == []


def test_an_unknown_source_permits_nobody():
    """Fail closed: a source added without a permission mapping notifies
    no one instead of everyone."""
    assert gates.permitted(_signal("weather"), ["sub-noah"]) == []


def test_a_member_holding_computer_use_is_kept_for_a_computer_signal():
    assert gates.permitted(_signal("computer"), ["sub-noah"]) == ["sub-noah"]


def test_a_member_lacking_computer_use_is_dropped_for_a_computer_signal():
    assert gates.permitted(_signal("computer"), ["sub-kid"]) == []


def test_a_duplicated_sub_is_deduped_preserving_order():
    """The filter's audience is an unconstrained model-produced list; a
    repeated sub must not yield two compose turns and two notice rows
    (fix round 1, item 3)."""
    assert gates.permitted(
        _signal("home"), ["sub-kid", "sub-noah", "sub-kid"]
    ) == ["sub-kid", "sub-noah"]


def test_the_window_parses_to_two_times():
    assert gates.parse_window("21:00-07:00") == (time(21, 0), time(7, 0))


@pytest.mark.parametrize("hour,quiet", [(22, True), (2, True), (6, True), (7, False), (12, False), (20, False), (21, True)])
def test_quiet_hours_wrap_around_midnight(hour, quiet):
    when = datetime(2026, 8, 23, hour, 0)
    assert gates.in_quiet_hours(when, "21:00-07:00") is quiet


@pytest.mark.parametrize("hour,quiet", [(13, True), (11, False), (15, False)])
def test_a_window_inside_one_day_does_not_wrap(hour, quiet):
    when = datetime(2026, 8, 23, hour, 0)
    assert gates.in_quiet_hours(when, "12:00-14:00") is quiet


def test_a_malformed_window_is_never_quiet():
    """A typo in configuration must not silence Eve permanently and must not
    raise into the pipeline."""
    assert gates.in_quiet_hours(datetime(2026, 8, 23, 3, 0), "nonsense") is False


def test_local_now_converts_into_the_members_zone():
    utc_evening = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)
    assert gates.local_now("America/Vancouver", utc_evening).hour == 20


def test_the_cap_window_starts_at_the_members_own_midnight():
    """Two members in two zones have two different days; one cap counted in
    UTC would cut off mid-evening for the western one."""
    now = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)
    vancouver = gates.day_start_utc("America/Vancouver", now)
    toronto = gates.day_start_utc("America/Toronto", now)
    assert vancouver == datetime(2026, 8, 23, 7, 0, tzinfo=UTC)
    # Same instant, two local days: 03:00Z is 20:00 on the 23rd in Vancouver
    # (PDT, -7) and 23:00 on the 23rd in Toronto (EDT, -4), so both members'
    # current local day is the 23rd — but its midnight lands on a different
    # UTC clock reading for each. Counting the cap in UTC would cut Vancouver
    # off mid-evening.
    assert toronto == datetime(2026, 8, 23, 4, 0, tzinfo=UTC)


def test_an_unknown_timezone_falls_back_to_utc_rather_than_raising():
    now = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)
    assert gates.day_start_utc("Mars/Olympus", now) == datetime(2026, 8, 24, tzinfo=UTC)
