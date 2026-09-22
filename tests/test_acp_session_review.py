"""A review session is a coding session with a different hint and a
different close. `_spawn` is faked wholesale, exactly as test_acp_session.py
does it: a real ACP subprocess in a unit test is an integration test wearing
the wrong marker.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from eve_computer.acp import session as session_mod
from eve_computer.acp.review import InvalidFindings


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("EVE_COMPUTER_CODE_DIR", str(tmp_path / "code"))
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    session_mod._SESSIONS.clear()
    session_mod._semaphore = None
    session_mod._review_semaphore = None
    yield
    get_computer_settings.cache_clear()
    session_mod._SESSIONS.clear()
    session_mod._semaphore = None
    session_mod._review_semaphore = None


def test_the_review_hint_forbids_editing_and_names_the_rubric():
    hint = session_mod.review_hint(["acme/repo"], 7, "abc123")

    assert "review" in hint.lower()
    assert "do not" in hint.lower()
    assert "review.json" in hint
    assert "prompts/code-review" in hint


def test_the_review_hint_requires_naming_skipped_sections():
    """Stack scoping is auditable or it is a hope."""
    hint = session_mod.review_hint(["acme/repo"], 7, "abc123")

    assert "skipped" in hint.lower()


def test_the_coding_hint_is_unchanged():
    """The default must stay exactly what coding sessions already get."""
    assert "do not push" in session_mod._SYSTEM_HINT
    assert "pull request" in session_mod._SYSTEM_HINT


async def test_closing_a_review_reads_the_findings_and_posts(tmp_path, monkeypatch):
    session = session_mod.Session(
        id="s1", agent="claude", model="m", repos=["acme/repo"],
        branch="", directory=tmp_path, kind="review", pr_number=7,
        merge_base="base-sha", head_sha="head-sha",
    )
    session_mod._SESSIONS["s1"] = session
    (tmp_path / "review.json").write_text(json.dumps({
        "summary": "One finding.",
        "findings": [{
            "severity": "nit", "axis": "readability",
            "file": "a.py", "line": 1, "body": "name",
        }],
    }))
    posted = AsyncMock(return_value={"posted": True, "skipped": False,
                                     "url": "https://x/7", "comments": 1,
                                     "demoted": 0})
    monkeypatch.setattr(session_mod.repo, "post_review", posted)
    monkeypatch.setattr(session_mod.repo, "remove_worktrees", AsyncMock())

    result = await session_mod.close_review("s1")

    assert result["posted"] is True
    assert result["findings"] == 1
    assert result["url"] == "https://x/7"
    assert posted.await_args.args[0] == "acme/repo"
    assert posted.await_args.args[1] == 7


async def test_a_missing_findings_file_fails_the_session(tmp_path, monkeypatch):
    """It does not fall back to scraping the transcript. A review that
    degrades into a summary looks like a review and is not one."""
    session = session_mod.Session(
        id="s1", agent="claude", model="m", repos=["acme/repo"],
        branch="", directory=tmp_path, kind="review", pr_number=7,
        merge_base="base", head_sha="head",
    )
    session_mod._SESSIONS["s1"] = session
    monkeypatch.setattr(session_mod.repo, "remove_worktrees", AsyncMock())

    with pytest.raises(InvalidFindings):
        await session_mod.close_review("s1")

    assert session.status == "failed"


async def test_the_worktree_is_torn_down_even_when_posting_fails(
    tmp_path, monkeypatch
):
    session = session_mod.Session(
        id="s1", agent="claude", model="m", repos=["acme/repo"],
        branch="", directory=tmp_path, kind="review", pr_number=7,
        merge_base="base", head_sha="head",
    )
    session_mod._SESSIONS["s1"] = session
    removed = AsyncMock()
    monkeypatch.setattr(session_mod.repo, "remove_worktrees", removed)

    with pytest.raises(InvalidFindings):
        await session_mod.close_review("s1")

    removed.assert_awaited_once()


async def _settle():
    """Let background driver tasks run to their next await point."""
    for _ in range(50):
        await asyncio.sleep(0)


async def test_a_review_session_uses_the_review_timeout_and_semaphore_not_the_coding_ones(
    monkeypatch,
):
    """Two properties Finding 3 exists to guarantee, driven through the real
    `_drive` loop with only the `_spawn` seam faked (same seam
    test_acp_session.py uses):

    (a) a review session serializes on `max_concurrent_reviews`, separate
    from `max_concurrent_sessions` - if it used the coding limiter instead,
    a second review session would proceed immediately despite
    `max_concurrent_reviews=1`, because `max_concurrent_sessions` here is
    set much higher.

    (b) a review session's per-turn timeout comes from
    `review_session_timeout_seconds`, not `session_turn_timeout_seconds` -
    the two are set to very different values below, and the timeout that
    actually reached `asyncio.wait_for` is captured and asserted on.
    """
    monkeypatch.setenv("EVE_COMPUTER_MAX_CONCURRENT_REVIEWS", "1")
    monkeypatch.setenv("EVE_COMPUTER_MAX_CONCURRENT_SESSIONS", "5")
    monkeypatch.setenv("EVE_COMPUTER_SESSION_TURN_TIMEOUT_SECONDS", "1800")
    monkeypatch.setenv("EVE_COMPUTER_REVIEW_SESSION_TIMEOUT_SECONDS", "99")
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    session_mod._semaphore = None
    session_mod._review_semaphore = None

    async def _add_review_worktree(repo, session_dir, pr_number, base_ref):
        return {"merge_base": "base-sha", "head_sha": "head-sha"}

    monkeypatch.setattr(session_mod.repo, "add_review_worktree", _add_review_worktree)
    monkeypatch.setattr(session_mod.repo, "remove_worktrees", AsyncMock())

    spawned: list[str] = []

    class StubConn:
        async def initialize(self, **kwargs):
            return type("R", (), {"agent_capabilities": None})()

        async def new_session(self, **kwargs):
            return type("R", (), {"session_id": "acp-1"})()

        async def prompt(self, session_id, prompt, **kwargs):
            return type("R", (), {"stop_reason": "end_turn"})()

        async def cancel(self, session_id, **kwargs):
            pass

        async def close_session(self, session_id, **kwargs):
            pass

    class NullManager:
        async def __aexit__(self, *exc):
            return False

    async def _spawn(client, argv, env, cwd):
        spawned.append(str(cwd))
        return StubConn(), NullManager()

    monkeypatch.setattr(session_mod, "_spawn", _spawn)

    captured_timeouts: list[float] = []
    real_wait_for = asyncio.wait_for

    async def _spy_wait_for(coro, timeout=None):
        captured_timeouts.append(timeout)
        return await real_wait_for(coro, timeout=timeout)

    monkeypatch.setattr(session_mod.asyncio, "wait_for", _spy_wait_for)

    await session_mod.create(
        "r1", "codex", "m", ["acme/repo"], "go", kind="review", pr_number=7,
    )
    await _settle()

    assert len(spawned) == 1
    assert captured_timeouts == [99]

    await session_mod.create(
        "r2", "codex", "m", ["acme/repo"], "go", kind="review", pr_number=8,
    )
    await _settle()

    # r2 must still be blocked on the review semaphore's one slot, not
    # running concurrently with r1.
    assert len(spawned) == 1
    assert session_mod.get("r2").status == "queued"

    await session_mod.kill("r1")
    await _settle()

    assert len(spawned) == 2
    assert session_mod.get("r2").status == "idle"

    await session_mod.kill("r2")
