"""Mirrors tests/test_ambient_sources_computer.py, including the 24-hour
re-derivation window: a signal suppressed by quiet hours or the daily cap
must be re-derivable on a later tick rather than lost the moment
supervisor.tick() stops returning it."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from eve_ambient.sources import coding


def _session(session_id="s1", status="finished", result=None):
    return {
        "id": session_id, "member_sub": "sub-noah", "thread_id": "t1",
        "goal": "fix the CalDAV client", "repos": ["acme/repo"],
        "status": status, "result": result if result is not None else {"prs": []},
        "finished_at": datetime.now(UTC),
    }


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(coding.supervisor, "tick", AsyncMock(return_value=[]))
    monkeypatch.setattr(coding.coding_store, "recently_resolved_sessions", AsyncMock(return_value=[]))


async def test_a_finished_session_names_its_pull_requests():
    coding.supervisor.tick.return_value = [
        _session(result={"summary": "done", "prs": [
            {"repo": "acme/repo", "commits": 2, "pr_url": "https://x/1"}
        ]})
    ]

    signals = await coding.poll("sub-noah")

    assert signals[0].source == "coding"
    assert "https://x/1" in signals[0].summary
    assert signals[0].payload["thread_id"] == "t1"


async def test_a_session_with_no_commits_says_so_rather_than_claiming_success():
    coding.supervisor.tick.return_value = [
        _session(result={"summary": "nothing to change", "prs": [
            {"repo": "acme/repo", "commits": 0, "pr_url": None}
        ]})
    ]

    signals = await coding.poll("sub-noah")

    assert "no changes" in signals[0].summary.lower()


async def test_a_blocked_session_carries_its_question():
    coding.supervisor.tick.return_value = [
        _session(status="blocked", result={"question": "Which staging DB?"})
    ]

    signals = await coding.poll("sub-noah")

    assert "Which staging DB?" in signals[0].summary


async def test_a_failed_session_reports_the_error():
    coding.supervisor.tick.return_value = [
        _session(status="failed", result={"error": "agent stopped: refusal"})
    ]

    assert "refusal" in (await coding.poll("sub-noah"))[0].summary


async def test_a_stale_session_says_it_never_reported_back():
    coding.supervisor.tick.return_value = [_session(status="stale", result=None)]

    assert "never reported back" in (await coding.poll("sub-noah"))[0].summary


async def test_a_suppressed_signal_is_re_derived_from_the_recent_window():
    coding.supervisor.tick.return_value = []
    coding.coding_store.recently_resolved_sessions.return_value = [_session()]

    assert len(await coding.poll("sub-noah")) == 1


async def test_this_ticks_row_wins_over_the_recent_window():
    # Both rows carry a PR link so each row's own summary text survives
    # _summary's rendering - a no-commit row deliberately reports "made no
    # changes" instead of the agent's claimed summary, which would make
    # this test unable to tell the rows apart.
    fresh = _session(result={"summary": "fresh", "prs": [{"repo": "r", "pr_url": "u"}]})
    coding.supervisor.tick.return_value = [fresh]
    coding.coding_store.recently_resolved_sessions.return_value = [
        _session(result={"summary": "stale copy", "prs": [{"repo": "r", "pr_url": "u"}]})
    ]

    signals = await coding.poll("sub-noah")

    assert len(signals) == 1
    assert "fresh" in signals[0].summary
