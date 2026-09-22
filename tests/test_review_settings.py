"""Settings and roster additions for EVE-27."""

from __future__ import annotations

import pytest

from eve.family import Family, UnknownMemberError


def test_review_is_disabled_by_default():
    from eve.settings import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    assert settings.review_enabled is False
    assert settings.review_webhook_secret == ""
    assert settings.review_repos == []


def test_review_session_bounds_are_tighter_than_coding_bounds():
    """A review holding a session slot for four hours has failed at
    something other than reviewing."""
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    settings = get_computer_settings()

    assert settings.review_session_timeout_seconds < settings.session_timeout_seconds
    assert settings.max_concurrent_reviews >= 1


def test_a_member_can_carry_a_github_login(tmp_path):
    roster = tmp_path / "family.yaml"
    roster.write_text(
        "members:\n"
        "  - sub: 'sub-noah'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    github_login: 'chalifournoah'\n"
        "    permissions: ['code.review']\n"
    )

    family = Family.from_yaml(roster)

    assert family.by_github_login("chalifournoah").sub == "sub-noah"


def test_an_unknown_github_login_is_rejected_rather_than_guessed(tmp_path):
    """A valid webhook signature proves GitHub sent the event, not that the
    labeller may spend Eve's tokens."""
    roster = tmp_path / "family.yaml"
    roster.write_text(
        "members:\n"
        "  - sub: 'sub-noah'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    github_login: 'chalifournoah'\n"
        "    permissions: []\n"
    )

    family = Family.from_yaml(roster)

    with pytest.raises(UnknownMemberError):
        family.by_github_login("a-stranger")


def test_a_member_without_a_github_login_never_matches(tmp_path):
    """`None` must not collide with a missing login on the lookup side."""
    roster = tmp_path / "family.yaml"
    roster.write_text(
        "members:\n"
        "  - sub: 'sub-kid'\n"
        "    name: 'Kid'\n"
        "    role: child\n"
        "    timezone: 'America/Vancouver'\n"
        "    permissions: []\n"
    )

    family = Family.from_yaml(roster)

    with pytest.raises(UnknownMemberError):
        family.by_github_login("")
