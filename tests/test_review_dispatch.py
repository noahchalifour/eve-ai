"""Starting a review. The allowlist, the idempotence check, and the
reviewer-is-not-the-implementer rule all bind here, before the box is
called and before any tokens are spent."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from eve.review import dispatch


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("EVE_REVIEW_ENABLED", "true")
    monkeypatch.setenv("EVE_REVIEW_REPOS", '["acme/repo"]')
    monkeypatch.setenv("EVE_REVIEW_DEFAULT_AGENT", "claude")
    monkeypatch.setenv("EVE_REVIEW_DEFAULT_MODEL", "anthropic/claude-sonnet-5")
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(dispatch.store, "review_exists_for", AsyncMock(return_value=False))
    monkeypatch.setattr(dispatch.store, "create_session", AsyncMock())
    monkeypatch.setattr(dispatch.store, "implementer_of", AsyncMock(return_value=None))
    monkeypatch.setattr(
        dispatch, "create_review_session", AsyncMock(return_value="ok")
    )


async def test_a_review_is_dispatched_and_recorded():
    result = await dispatch.start(
        repo="acme/repo", pr_number=7, head_sha="abc", base_ref="main",
        member_sub="sub-noah",
    )

    assert not result.startswith("error:")
    dispatch.create_review_session.assert_awaited_once()
    dispatch.store.create_session.assert_awaited_once()
    assert dispatch.store.create_session.await_args.kwargs["kind"] == "review"
    assert dispatch.store.create_session.await_args.kwargs["pr_number"] == 7


async def test_a_repository_outside_the_allowlist_is_refused():
    """A webhook can name any repository; only configured ones spend."""
    result = await dispatch.start(
        repo="someone-else/private", pr_number=1, head_sha="abc",
        base_ref="main", member_sub="sub-noah",
    )

    assert result.startswith("error:")
    dispatch.create_review_session.assert_not_awaited()


async def test_an_already_reviewed_commit_is_not_reviewed_again():
    dispatch.store.review_exists_for.return_value = True

    result = await dispatch.start(
        repo="acme/repo", pr_number=7, head_sha="abc", base_ref="main",
        member_sub="sub-noah",
    )

    assert "already" in result.lower()
    dispatch.create_review_session.assert_not_awaited()


async def test_the_row_is_written_only_after_the_box_accepts():
    """Same ordering rule dispatch_coding_task documents: a row for a
    session the box never heard of would be polled forever."""
    dispatch.create_review_session.return_value = "error: eve-computer unavailable"

    result = await dispatch.start(
        repo="acme/repo", pr_number=7, head_sha="abc", base_ref="main",
        member_sub="sub-noah",
    )

    assert result.startswith("error:")
    dispatch.store.create_session.assert_not_awaited()


async def test_an_eve_authored_pull_request_avoids_its_implementer():
    dispatch.store.implementer_of.return_value = ("claude", "anthropic/claude-sonnet-5")

    await dispatch.start(
        repo="acme/repo", pr_number=7, head_sha="abc", base_ref="main",
        member_sub="sub-noah",
    )

    kwargs = dispatch.store.create_session.await_args.kwargs
    assert kwargs["agent"] != "claude"


async def test_review_disabled_refuses_before_anything_is_spent(monkeypatch):
    monkeypatch.setenv("EVE_REVIEW_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()

    result = await dispatch.start(
        repo="acme/repo", pr_number=7, head_sha="abc", base_ref="main",
        member_sub="sub-noah",
    )

    assert result.startswith("error:")
    dispatch.create_review_session.assert_not_awaited()
