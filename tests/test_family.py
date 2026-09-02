from pathlib import Path

import pytest

from eve.family import Family, UnknownMemberError

ROSTER = """
members:
  - sub: "sub-noah"
    name: "Noah"
    role: adult
    timezone: "America/Toronto"
    permissions: [home.control, mail.send, spend]
  - sub: "sub-kid"
    name: "Kid"
    role: child
    timezone: "America/Toronto"
    permissions: [home.control]
"""


@pytest.fixture
def roster(tmp_path):
    path = tmp_path / "family.yaml"
    path.write_text(ROSTER)
    return Family.from_yaml(path)


def test_lookup_by_subject(roster):
    noah = roster.get("sub-noah")
    assert noah.name == "Noah"
    assert noah.role == "adult"
    assert noah.timezone == "America/Toronto"


def test_permissions_are_checked_by_name(roster):
    assert roster.get("sub-noah").can("spend") is True
    assert roster.get("sub-kid").can("spend") is False


def test_unknown_subject_raises(roster):
    with pytest.raises(UnknownMemberError, match="sub-nobody"):
        roster.get("sub-nobody")


def test_member_is_immutable(roster):
    with pytest.raises(Exception):
        roster.get("sub-noah").name = "Someone Else"


def test_members_are_iterable_in_roster_order(roster):
    """The poll loop walks the roster per member; without this it would have
    to reach into Family's private dict."""
    assert [m.name for m in roster.members()] == ["Noah", "Kid"]


def test_the_shipped_roster_grants_calendar_read_to_someone():
    """A guard, not a roster snapshot: if this grant were ever dropped from
    family.yaml, nobody would receive a calendar notification and nothing
    else would say so."""
    family = Family.from_yaml(Path("family.yaml"))
    assert any(m.can("calendar.read") for m in family.members())


def test_a_member_without_a_wardrobe_album_gets_none(tmp_path):
    path = tmp_path / "family.yaml"
    path.write_text(
        "members:\n"
        "  - sub: 'sub-1'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
    )
    family = Family.from_yaml(path)
    assert family.get("sub-1").wardrobe_album is None


def test_a_wardrobe_album_is_read_from_the_roster(tmp_path):
    path = tmp_path / "family.yaml"
    path.write_text(
        "members:\n"
        "  - sub: 'sub-1'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    wardrobe_album: 'album-uuid-1'\n"
    )
    family = Family.from_yaml(path)
    assert family.get("sub-1").wardrobe_album == "album-uuid-1"
