from datetime import UTC, datetime
from unittest.mock import AsyncMock

from eve_ambient.sources import computer


async def test_a_finished_task_becomes_a_signal_addressed_to_its_member(monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setattr(
        computer.poller, "sync",
        AsyncMock(return_value=[{
            "id": "t1", "member_sub": "sub-noah", "thread_id": "thread-1",
            "goal": "book the flight", "status": "finished",
            "result": {"summary": "Booked WS123 for the 14th."},
            "finished_at": now,
        }]),
    )
    signals = await computer.poll("")
    assert len(signals) == 1
    signal = signals[0]
    assert signal.source == "computer"
    assert signal.key == "t1"
    assert signal.member_sub == "sub-noah"
    assert "book the flight" in signal.summary
    assert signal.payload["thread_id"] == "thread-1"
    assert signal.payload["result"] == {"summary": "Booked WS123 for the 14th."}


async def test_a_failed_task_says_so_in_the_summary(monkeypatch):
    monkeypatch.setattr(
        computer.poller, "sync",
        AsyncMock(return_value=[{
            "id": "t1", "member_sub": "sub-noah", "thread_id": "thread-1",
            "goal": "book the flight", "status": "failed",
            "result": {"error": "RuntimeError: no such airline"},
            "finished_at": datetime.now(UTC),
        }]),
    )
    signals = await computer.poll("")
    assert "failed" in signals[0].summary.lower()
    assert "no such airline" in signals[0].summary


async def test_a_stale_task_says_so_in_the_summary(monkeypatch):
    monkeypatch.setattr(
        computer.poller, "sync",
        AsyncMock(return_value=[{
            "id": "t1", "member_sub": "sub-noah", "thread_id": "thread-1",
            "goal": "book the flight", "status": "stale", "result": None,
            "finished_at": datetime.now(UTC),
        }]),
    )
    signals = await computer.poll("")
    assert "stale" in signals[0].summary.lower()


async def test_never_recurs_once_seen(monkeypatch):
    """A task id is a one-shot key - the same task never finishes twice, so
    the signal carries no cooldown to re-trigger on."""
    monkeypatch.setattr(
        computer.poller, "sync",
        AsyncMock(return_value=[{
            "id": "t1", "member_sub": "sub-noah", "thread_id": "thread-1",
            "goal": "goal", "status": "finished", "result": {},
            "finished_at": datetime.now(UTC),
        }]),
    )
    signals = await computer.poll("")
    assert signals[0].cooldown_hours == 0


async def test_no_resolved_tasks_is_no_signals(monkeypatch):
    monkeypatch.setattr(computer.poller, "sync", AsyncMock(return_value=[]))
    assert await computer.poll("") == []
