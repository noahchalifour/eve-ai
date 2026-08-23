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
