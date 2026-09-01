from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from eve_ambient.sources import computer


@pytest.fixture(autouse=True)
def _no_recently_resolved_by_default(monkeypatch):
    """Every test in this file that doesn't care about the 24-hour
    re-derivation window gets an empty `recently_resolved_tasks`, so `poll()`
    behaves exactly as it did before that lookback was added unless a test
    opts into exercising it."""
    monkeypatch.setattr(
        computer.computer_store, "recently_resolved_tasks", AsyncMock(return_value=[]),
    )


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


async def test_carries_a_24_hour_retry_window(monkeypatch):
    """Not "never recurs": a suppressed or deferred signal must get a real
    retry window (fix wave item 1), so the cooldown matches the 24-hour
    lookback `recently_resolved_tasks` re-derives signals over."""
    monkeypatch.setattr(
        computer.poller, "sync",
        AsyncMock(return_value=[{
            "id": "t1", "member_sub": "sub-noah", "thread_id": "thread-1",
            "goal": "goal", "status": "finished", "result": {},
            "finished_at": datetime.now(UTC),
        }]),
    )
    monkeypatch.setattr(
        computer.computer_store, "recently_resolved_tasks", AsyncMock(return_value=[]),
    )
    signals = await computer.poll("")
    assert signals[0].cooldown_hours == 24


async def test_no_resolved_tasks_is_no_signals(monkeypatch):
    monkeypatch.setattr(computer.poller, "sync", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        computer.computer_store, "recently_resolved_tasks", AsyncMock(return_value=[]),
    )
    assert await computer.poll("") == []


async def test_a_task_missed_by_this_ticks_sync_is_still_recovered(monkeypatch):
    """The scenario the fix wave describes: `poller.sync()` only returns a
    row on the exact tick it transitions. A task whose signal was suppressed
    or deferred on that tick no longer shows up in `sync()`'s return value on
    later ticks - it must still surface via `recently_resolved_tasks`, or the
    result is lost forever the moment delivery fails once."""
    monkeypatch.setattr(computer.poller, "sync", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        computer.computer_store, "recently_resolved_tasks",
        AsyncMock(return_value=[{
            "id": "t1", "member_sub": "sub-noah", "thread_id": "thread-1",
            "goal": "book the flight", "status": "finished",
            "result": {"summary": "Booked WS123 for the 14th."},
            "finished_at": datetime.now(UTC),
        }]),
    )
    signals = await computer.poll("")
    assert len(signals) == 1
    assert signals[0].key == "t1"
    assert signals[0].cooldown_hours == 24


async def test_a_task_in_both_sync_and_recently_resolved_is_not_duplicated(monkeypatch):
    """A task that resolved on this exact tick appears in both `sync()`'s
    return value and `recently_resolved_tasks`'s 24-hour window - it must
    only produce one Signal."""
    task = {
        "id": "t1", "member_sub": "sub-noah", "thread_id": "thread-1",
        "goal": "book the flight", "status": "finished",
        "result": {"summary": "done"}, "finished_at": datetime.now(UTC),
    }
    monkeypatch.setattr(computer.poller, "sync", AsyncMock(return_value=[task]))
    monkeypatch.setattr(
        computer.computer_store, "recently_resolved_tasks",
        AsyncMock(return_value=[dict(task)]),
    )
    signals = await computer.poll("")
    assert len(signals) == 1
    assert signals[0].key == "t1"
