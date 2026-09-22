"""A review's `done` closes a review, not a coding session."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from eve.coding import supervisor


def _row(kind="review"):
    return {
        "id": "s1", "member_sub": "sub-noah", "thread_id": "t1",
        "goal": "review acme/repo#7", "repos": ["acme/repo"], "context": "",
        "status": "running", "cursor": 0, "kind": kind, "pr_number": 7,
        "head_sha": "abc", "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC), "supervisor_turns": 0,
    }


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(supervisor.store, "advance_cursor", AsyncMock())
    monkeypatch.setattr(supervisor.store, "bump_supervisor_turns", AsyncMock(return_value=1))
    monkeypatch.setattr(supervisor.store, "mark_resolved", AsyncMock())
    monkeypatch.setattr(supervisor.store, "set_status", AsyncMock())
    monkeypatch.setattr(
        supervisor, "close_review_session",
        AsyncMock(return_value={"posted": True, "findings": 2, "url": "https://x/7",
                                "counts": {"critical": 1, "nit": 1}}),
    )
    monkeypatch.setattr(supervisor, "close_coding_session", AsyncMock(return_value={"prs": []}))


async def test_a_finished_review_closes_the_review_not_a_coding_session(monkeypatch):
    monkeypatch.setattr(
        supervisor, "get_coding_session",
        AsyncMock(return_value={"status": "idle", "cursor": 1, "pending": [],
                                "turns": [{"role": "agent", "text": "findings written"}]}),
    )
    monkeypatch.setattr(
        supervisor, "decide",
        AsyncMock(return_value=supervisor.Decision(action="done", text="reviewed")),
    )
    settings = type("S", (), {
        "coding_session_timeout_seconds": 99999,
        "coding_max_supervisor_turns": 30,
    })()

    result = await supervisor._advance(
        _row(), datetime.now(UTC), supervisor.timedelta(minutes=120), settings
    )

    supervisor.close_review_session.assert_awaited_once_with("s1")
    supervisor.close_coding_session.assert_not_awaited()
    assert result["result"]["findings"] == 2
    assert result["result"]["url"] == "https://x/7"


async def test_a_finished_coding_session_still_closes_normally(monkeypatch):
    monkeypatch.setattr(
        supervisor, "get_coding_session",
        AsyncMock(return_value={"status": "idle", "cursor": 1, "pending": [],
                                "turns": [{"role": "agent", "text": "done"}]}),
    )
    monkeypatch.setattr(
        supervisor, "decide",
        AsyncMock(return_value=supervisor.Decision(action="done", text="done")),
    )
    settings = type("S", (), {
        "coding_session_timeout_seconds": 99999,
        "coding_max_supervisor_turns": 30,
    })()

    await supervisor._advance(
        _row(kind="code"), datetime.now(UTC),
        supervisor.timedelta(minutes=120), settings,
    )

    supervisor.close_coding_session.assert_awaited_once()
    supervisor.close_review_session.assert_not_awaited()


async def test_a_review_that_could_not_be_closed_is_a_failure(monkeypatch):
    """A 422 from the box means no usable review.json. Reporting that as a
    clean review would be the silent degradation the spec forbids."""
    monkeypatch.setattr(
        supervisor, "get_coding_session",
        AsyncMock(return_value={"status": "idle", "cursor": 1, "pending": [],
                                "turns": [{"role": "agent", "text": "done"}]}),
    )
    monkeypatch.setattr(
        supervisor, "decide",
        AsyncMock(return_value=supervisor.Decision(action="done", text="reviewed")),
    )
    supervisor.close_review_session.return_value = None
    settings = type("S", (), {
        "coding_session_timeout_seconds": 99999,
        "coding_max_supervisor_turns": 30,
    })()

    result = await supervisor._advance(
        _row(), datetime.now(UTC), supervisor.timedelta(minutes=120), settings
    )

    assert result["status"] == "failed"


def test_the_review_prompt_tells_the_supervisor_what_done_means():
    prompt = supervisor.system_prompt_for("review")

    assert "review.json" in prompt or "findings" in prompt.lower()
