"""Addressing feedback on Eve's own pull requests (EVE-31): only hers, only
from people she works for, one at a time, capped, and on behalf of the
member who asked for the change."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from eve.coding import followup

URL = "https://github.com/acme/repo/pull/7"
ORIGIN = {
    "id": "code-1", "member_sub": "sub-noah", "thread_id": "thread-1",
    "agent": "dsh", "model": "deepseek/v4", "context": "remembered things",
    "linear_issue_id": "issue-31",
}


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    roster = tmp_path / "family.yaml"
    roster.write_text(
        "members:\n"
        "  - sub: 'sub-noah'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    github_login: 'chalifournoah'\n"
        "    permissions: ['code.delegate']\n"
        "  - sub: 'sub-eve'\n"
        "    name: 'Eve bot'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    github_login: 'eve-bot'\n"
        "    permissions: []\n"
    )
    monkeypatch.setenv("EVE_FAMILY_FILE", str(roster))
    monkeypatch.setenv("EVE_PR_FOLLOWUP_ENABLED", "true")
    monkeypatch.setenv("EVE_GITHUB_LOGIN", "eve-bot")
    monkeypatch.setenv("EVE_PR_FOLLOWUP_MAX_PER_PR", "5")
    from eve.family import get_family
    from eve.settings import get_settings

    get_settings.cache_clear()
    get_family.cache_clear()
    yield
    get_settings.cache_clear()
    get_family.cache_clear()


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(followup.store, "origin_of_pr", AsyncMock(return_value=ORIGIN))
    monkeypatch.setattr(followup.store, "pr_session_stats", AsyncMock(
        return_value={"total": 0, "live": 0, "latest": None}
    ))
    monkeypatch.setattr(followup.store, "create_session", AsyncMock())
    monkeypatch.setattr(followup, "create_address_session", AsyncMock(return_value="ok"))
    monkeypatch.setattr(followup, "kill_coding_session", AsyncMock())


def test_eve_is_never_a_trusted_author_of_her_own_feedback():
    """Her replies and her own EVE-27 reviews must not wake her up."""
    assert followup.trusted_authors() == ["chalifournoah"]
    assert followup.is_trusted("ChalifourNoah")
    assert not followup.is_trusted("eve-bot")
    assert not followup.is_trusted("a-stranger")
    assert not followup.is_trusted("")


async def test_feedback_on_eves_pr_starts_an_address_session():
    result = await followup.start("acme/repo", 7, URL)

    assert result.startswith("addressing")
    args = followup.create_address_session.await_args.args
    # The implementer that opened the PR answers for it.
    assert args[1:5] == ("dsh", "deepseek/v4", "acme/repo", 7)
    assert args[6] == ["chalifournoah"]
    row = followup.store.create_session.await_args.kwargs
    assert row["kind"] == "address"
    assert row["thread_id"] == "thread-1"
    assert row["member_sub"] == "sub-noah"
    assert row["linear_issue_id"] == "issue-31"
    # Not the origin's Linear session: its unique index belongs to it.
    assert row["linear_session_id"] is None


async def test_someone_elses_pr_is_left_alone():
    followup.store.origin_of_pr.return_value = None

    assert "not opened by Eve" in await followup.start("acme/repo", 7, URL)
    followup.create_address_session.assert_not_awaited()


async def test_one_follow_up_at_a_time():
    followup.store.pr_session_stats.return_value = {"total": 1, "live": 1, "latest": None}

    assert "already being addressed" in await followup.start("acme/repo", 7, URL)
    followup.create_address_session.assert_not_awaited()


async def test_follow_ups_are_capped_per_pr():
    followup.store.pr_session_stats.return_value = {"total": 5, "live": 0, "latest": None}

    assert "cap" in await followup.start("acme/repo", 7, URL)
    followup.create_address_session.assert_not_awaited()


async def test_it_is_opt_in(monkeypatch):
    monkeypatch.setenv("EVE_PR_FOLLOWUP_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()

    assert "disabled" in await followup.start("acme/repo", 7, URL)
    followup.store.origin_of_pr.assert_not_awaited()


async def test_the_members_grant_is_checked_as_it_stands_now():
    followup.store.origin_of_pr.return_value = {**ORIGIN, "member_sub": "sub-eve"}

    assert "can no longer" in await followup.start("acme/repo", 7, URL)
    followup.create_address_session.assert_not_awaited()


async def test_a_box_refusal_records_nothing():
    followup.create_address_session.return_value = "error: eve-computer unavailable"

    assert (await followup.start("acme/repo", 7, URL)).startswith("error:")
    followup.store.create_session.assert_not_awaited()


async def test_a_failed_record_kills_the_box_session():
    followup.store.create_session.side_effect = RuntimeError("db down")

    assert (await followup.start("acme/repo", 7, URL)).startswith("error:")
    followup.kill_coding_session.assert_awaited_once()
