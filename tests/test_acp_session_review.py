"""A review session is a coding session with a different hint and a
different close. `_spawn` is faked wholesale, exactly as test_acp_session.py
does it: a real ACP subprocess in a unit test is an integration test wearing
the wrong marker.
"""

from __future__ import annotations

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
    yield
    get_computer_settings.cache_clear()
    session_mod._SESSIONS.clear()


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
