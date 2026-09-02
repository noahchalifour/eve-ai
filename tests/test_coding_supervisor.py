"""The four-way decision, and what each outcome does to the row.

The test that matters most is `test_escalate_parks_the_session_alive`:
escalating and then discarding the session would throw away the very thing
the member's answer is for, and the two features would not compose."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from eve.coding import supervisor
from eve.coding.supervisor import Decision


def _row(session_id="s1", status="running", cursor=0, updated_at=None):
    return {
        "id": session_id, "member_sub": "sub-noah", "thread_id": "t1",
        "goal": "fix the CalDAV client", "agent": "codex", "model": "m",
        "repos": ["acme/repo"], "context": "Noah prefers httpx.",
        "status": status, "cursor": cursor, "supervisor_turns": 0,
        "updated_at": updated_at or datetime.now(UTC),
        "created_at": datetime.now(UTC),
    }


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setenv("EVE_CODING_SESSION_STALE_MINUTES", "60")
    monkeypatch.setenv("EVE_CODING_MAX_SUPERVISOR_TURNS", "3")
    monkeypatch.setenv("EVE_CODING_SESSION_TIMEOUT_SECONDS", "28800")
    from eve.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(supervisor.store, "live_sessions", AsyncMock(return_value=[]))
    monkeypatch.setattr(supervisor.store, "advance_cursor", AsyncMock())
    monkeypatch.setattr(supervisor.store, "mark_resolved", AsyncMock())
    monkeypatch.setattr(supervisor.store, "set_status", AsyncMock())
    monkeypatch.setattr(supervisor.store, "bump_supervisor_turns", AsyncMock(return_value=1))
    monkeypatch.setattr(supervisor, "prompt_coding_session", AsyncMock(return_value="ok"))
    monkeypatch.setattr(supervisor, "close_coding_session", AsyncMock(return_value={"prs": []}))
    monkeypatch.setattr(supervisor, "kill_coding_session", AsyncMock(return_value="ok"))
    yield
    get_settings.cache_clear()


def _box(status="idle", turns=None, pending=None, cursor=2):
    return {
        "status": status,
        "turns": turns if turns is not None else [{"role": "agent", "text": "Which auth library?"}],
        "pending": pending or [],
        "cursor": cursor,
        "activity": [],
        "error": "",
    }


async def test_a_running_session_is_left_alone(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box("running")))
    decide = AsyncMock()
    monkeypatch.setattr(supervisor, "decide", decide)

    assert await supervisor.tick() == []
    decide.assert_not_awaited()


async def test_reply_sends_the_composed_prompt_and_keeps_the_session_live(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box()))
    monkeypatch.setattr(
        supervisor, "decide",
        AsyncMock(return_value=Decision(action="reply", text="Use httpx.")),
    )

    resolved = await supervisor.tick()

    supervisor.prompt_coding_session.assert_awaited_once_with("s1", "Use httpx.", kind="reply")
    supervisor.store.advance_cursor.assert_awaited_once_with("s1", 2)
    assert resolved == []


async def test_done_closes_the_session_and_reports_the_prs(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box()))
    monkeypatch.setattr(
        supervisor, "decide", AsyncMock(return_value=Decision(action="done", text="All set."))
    )
    supervisor.close_coding_session.return_value = {
        "prs": [{"repo": "acme/repo", "commits": 2, "pr_url": "https://x/1"}]
    }

    resolved = await supervisor.tick()

    supervisor.close_coding_session.assert_awaited_once_with("s1")
    assert resolved[0]["status"] == "finished"
    assert resolved[0]["result"]["prs"][0]["pr_url"] == "https://x/1"


async def test_escalate_parks_the_session_alive(monkeypatch):
    """The subprocess and the worktrees stay up so the member's answer can
    resume this same session through send_to_coding_session."""
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box()))
    monkeypatch.setattr(
        supervisor, "decide",
        AsyncMock(return_value=Decision(action="escalate", text="Which staging DB?")),
    )

    resolved = await supervisor.tick()

    supervisor.close_coding_session.assert_not_awaited()
    supervisor.store.set_status.assert_awaited_once_with("s1", "blocked")
    assert resolved[0]["status"] == "blocked"
    assert "Which staging DB?" in resolved[0]["result"]["question"]


async def test_an_already_blocked_session_is_not_re_escalated(monkeypatch):
    """It is waiting on a human. Asking again every 20 seconds would be a
    notification loop, not a conversation."""
    supervisor.store.live_sessions.return_value = [_row(status="blocked")]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box()))
    decide = AsyncMock()
    monkeypatch.setattr(supervisor, "decide", decide)

    assert await supervisor.tick() == []
    decide.assert_not_awaited()


async def test_a_pending_interjection_reaches_the_decision(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(
        supervisor, "get_coding_session",
        AsyncMock(return_value=_box(pending=["use httpx instead"])),
    )
    decide = AsyncMock(return_value=Decision(action="reply", text="Switch to httpx."))
    monkeypatch.setattr(supervisor, "decide", decide)

    await supervisor.tick()

    assert decide.await_args.args[2] == ["use httpx instead"]


async def test_a_blocked_session_wakes_when_a_member_interjects(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row(status="blocked")]
    monkeypatch.setattr(
        supervisor, "get_coding_session",
        AsyncMock(return_value=_box(pending=["the staging one"])),
    )
    monkeypatch.setattr(
        supervisor, "decide", AsyncMock(return_value=Decision(action="reply", text="Use staging."))
    )

    await supervisor.tick()

    supervisor.prompt_coding_session.assert_awaited_once_with("s1", "Use staging.", kind="reply")


async def test_a_failed_session_on_the_box_resolves_as_failed(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(
        supervisor, "get_coding_session",
        AsyncMock(return_value={**_box(status="failed"), "error": "agent stopped: refusal"}),
    )

    resolved = await supervisor.tick()

    assert resolved[0]["status"] == "failed"
    assert "refusal" in resolved[0]["result"]["error"]


async def test_a_box_that_stops_answering_goes_stale_after_the_timeout(monkeypatch):
    stale = datetime.now(UTC) - timedelta(minutes=90)
    supervisor.store.live_sessions.return_value = [_row(updated_at=stale)]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=None))

    resolved = await supervisor.tick()

    assert resolved[0]["status"] == "stale"


async def test_a_box_that_stops_answering_briefly_is_left_alone(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=None))

    assert await supervisor.tick() == []


async def test_blowing_the_supervisor_budget_parks_and_asks_in_english(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box()))
    supervisor.store.bump_supervisor_turns.return_value = 4  # cap is 3
    decide = AsyncMock()
    monkeypatch.setattr(supervisor, "decide", decide)

    resolved = await supervisor.tick()

    decide.assert_not_awaited()
    assert resolved[0]["status"] == "blocked"
    assert "back and forth" in resolved[0]["result"]["question"]


async def test_a_session_past_its_wall_clock_bound_is_closed_out(monkeypatch):
    """The spec's per-session wall-clock bound. Without it a session parked
    on `blocked` that nobody ever answers sits live forever, holding a
    subprocess, a worktree, and a semaphore slot."""
    old = datetime.now(UTC) - timedelta(hours=9)
    supervisor.store.live_sessions.return_value = [{**_row(status="blocked"), "created_at": old}]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box()))
    monkeypatch.setattr(supervisor, "kill_coding_session", AsyncMock(return_value="ok"))
    decide = AsyncMock()
    monkeypatch.setattr(supervisor, "decide", decide)

    resolved = await supervisor.tick()

    decide.assert_not_awaited()
    supervisor.kill_coding_session.assert_awaited_once_with("s1")
    assert resolved[0]["status"] == "failed"
    assert "too long" in resolved[0]["result"]["error"]


async def test_a_session_inside_its_wall_clock_bound_is_left_running(monkeypatch):
    supervisor.store.live_sessions.return_value = [
        {**_row(), "created_at": datetime.now(UTC) - timedelta(minutes=5)}
    ]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box("running")))

    assert await supervisor.tick() == []


async def test_one_bad_session_does_not_stop_the_others(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row("s1"), _row("s2")]

    async def _get(session_id, since=0):
        if session_id == "s1":
            raise RuntimeError("boom")
        return _box(status="failed")

    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(side_effect=_get))

    resolved = await supervisor.tick()

    assert [r["id"] for r in resolved] == ["s2"]


async def test_decide_asks_tier_code_and_gets_a_structured_answer(monkeypatch):
    """The decision is one call, on the tier that exists for code."""
    captured = {}

    class FakeModel:
        def with_structured_output(self, schema):
            captured["schema"] = schema
            return self

        async def ainvoke(self, messages):
            captured["messages"] = messages
            return Decision(action="done", text="Opened the PR.")

    def _get_model(tier):
        captured["tier"] = tier
        return FakeModel()

    monkeypatch.setattr(supervisor, "get_model", _get_model)

    decision = await supervisor.decide(
        _row(), [{"role": "agent", "text": "Opened PR #4."}], []
    )

    from eve.models import Tier

    assert captured["tier"] is Tier.CODE
    assert captured["schema"] is Decision
    assert decision.action == "done"
    # The recall snapshot and the goal both reach the model.
    prompt = str(captured["messages"])
    assert "Noah prefers httpx." in prompt
    assert "fix the CalDAV client" in prompt


async def test_a_decision_call_that_fails_leaves_the_session_alone(monkeypatch):
    supervisor.store.live_sessions.return_value = [_row()]
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value=_box()))
    monkeypatch.setattr(supervisor, "decide", AsyncMock(side_effect=RuntimeError("model down")))

    assert await supervisor.tick() == []
    supervisor.store.mark_resolved.assert_not_awaited()
