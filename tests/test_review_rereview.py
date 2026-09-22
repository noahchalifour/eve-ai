"""Re-reviewing when new commits land (EVE-32): the opt-in, the cap, the
one-at-a-time rule, and the incremental `since_sha`."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from eve.review import dispatch

# Captured before the autouse fixture replaces it.
_REAL_START = dispatch.start

PREVIOUS = {"member_sub": "sub-noah", "head_sha": "old", "pr_number": 7}


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
        "    permissions: ['code.review']\n"
        "  - sub: 'sub-kid'\n"
        "    name: 'Kid'\n"
        "    role: child\n"
        "    timezone: 'America/Vancouver'\n"
        "    permissions: []\n"
    )
    monkeypatch.setenv("EVE_FAMILY_FILE", str(roster))
    monkeypatch.setenv("EVE_REVIEW_ENABLED", "true")
    monkeypatch.setenv("EVE_REVIEW_REPOS", '["acme/repo"]')
    monkeypatch.setenv("EVE_REVIEW_MAX_PER_PR", "3")
    from eve.family import get_family
    from eve.settings import get_settings

    get_settings.cache_clear()
    get_family.cache_clear()
    yield
    get_settings.cache_clear()
    get_family.cache_clear()


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(dispatch.store, "latest_review_for", AsyncMock(return_value=PREVIOUS))
    monkeypatch.setattr(dispatch.store, "pr_session_stats", AsyncMock(
        return_value={"total": 1, "live": 0, "latest": None}
    ))
    monkeypatch.setattr(dispatch, "start", AsyncMock(return_value="reviewing"))


async def _push(head_sha="new"):
    return await dispatch.restart_on_push(
        repo="acme/repo", pr_number=7, head_sha=head_sha, base_ref="main"
    )


async def test_a_push_to_a_reviewed_pr_is_rereviewed_incrementally():
    assert await _push() == "reviewing"

    kwargs = dispatch.start.await_args.kwargs
    assert kwargs["since_sha"] == "old"
    assert kwargs["head_sha"] == "new"
    # On behalf of whoever asked for the review, not whoever pushed.
    assert kwargs["member_sub"] == "sub-noah"


async def test_a_pr_nobody_asked_about_is_not_opted_in_by_a_push():
    dispatch.store.latest_review_for.return_value = None

    result = await _push()

    assert "never reviewed" in result
    dispatch.start.assert_not_awaited()


async def test_the_cap_counts_the_first_review():
    dispatch.store.pr_session_stats.return_value = {"total": 3, "live": 0, "latest": None}

    result = await _push()

    assert "cap" in result
    dispatch.start.assert_not_awaited()


async def test_a_running_review_is_not_joined_by_a_second():
    dispatch.store.pr_session_stats.return_value = {"total": 1, "live": 1, "latest": None}

    assert "still running" in await _push()
    dispatch.start.assert_not_awaited()


async def test_the_same_head_is_not_reviewed_twice():
    assert "already been reviewed" in await _push(head_sha="old")
    dispatch.start.assert_not_awaited()


async def test_the_members_grant_is_checked_as_it_stands_now():
    dispatch.store.latest_review_for.return_value = {**PREVIOUS, "member_sub": "sub-kid"}

    assert "can no longer" in await _push()
    dispatch.start.assert_not_awaited()


async def test_review_on_push_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("EVE_REVIEW_ON_PUSH", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()

    assert "disabled" in await _push()
    dispatch.start.assert_not_awaited()


async def test_start_forwards_since_sha_to_the_box(monkeypatch):
    """The real `start`, with the box faked: the incremental base reaches it."""
    monkeypatch.setattr(dispatch.store, "review_exists_for", AsyncMock(return_value=False))
    monkeypatch.setattr(dispatch.store, "create_session", AsyncMock())
    monkeypatch.setattr(dispatch.store, "implementer_of", AsyncMock(return_value=None))
    created = AsyncMock(return_value="ok")
    monkeypatch.setattr(dispatch, "create_review_session", created)

    await _REAL_START(
        repo="acme/repo", pr_number=7, head_sha="new", base_ref="main",
        member_sub="sub-noah", since_sha="old",
    )

    assert created.await_args.kwargs["since_sha"] == "old"
