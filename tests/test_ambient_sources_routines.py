from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from eve_ambient.sources import routines

DUE = {
    "id": "r-1",
    "member_sub": "sub-noah",
    "title": "Flights",
    "instruction": "Check Aeroplan fares YVR to SJD.",
    "cadence": {"daily_at": "08:00"},
    "timezone": "America/Vancouver",
    "status": "active",
    "scheduled_for": datetime(2026, 1, 11, 16, 0, tzinfo=UTC),
    "last_run_at": None,
    "expires_at": None,
    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
}


@pytest.fixture(autouse=True)
def _store(monkeypatch):
    monkeypatch.setattr(routines.store, "claim_due", AsyncMock(return_value=[]))
    monkeypatch.setattr(routines.store, "schedule_next", AsyncMock())
    monkeypatch.setattr(routines.store, "record_run", AsyncMock())
    monkeypatch.setattr(routines.store, "expire", AsyncMock())


async def test_a_due_routine_becomes_a_signal_addressed_to_its_member(monkeypatch):
    monkeypatch.setattr(routines.store, "claim_due", AsyncMock(return_value=[DUE]))

    signals = await routines.poll("")

    assert len(signals) == 1
    signal = signals[0]
    assert signal.source == "routines"
    assert signal.member_sub == "sub-noah"
    assert signal.payload["routine_id"] == "r-1"
    assert signal.payload["instruction"] == DUE["instruction"]


async def test_the_key_carries_the_occurrence_not_just_the_routine(monkeypatch):
    """Two firings of the same routine are two signals. A key of just the
    routine id would make every run after the first look like a duplicate and
    be dropped by the cooldown check forever."""
    monkeypatch.setattr(routines.store, "claim_due", AsyncMock(return_value=[DUE]))

    signals = await routines.poll("")

    assert signals[0].key == "r-1:2026-01-11T16:00:00+00:00"


async def test_firing_schedules_the_next_occurrence(monkeypatch):
    schedule = AsyncMock()
    monkeypatch.setattr(routines.store, "claim_due", AsyncMock(return_value=[DUE]))
    monkeypatch.setattr(routines.store, "schedule_next", schedule)

    await routines.poll("")

    schedule.assert_awaited_once()
    routine_id, when = schedule.await_args.args
    assert routine_id == "r-1"
    assert when > datetime.now(UTC)


async def test_an_expired_routine_is_retired_instead_of_fired(monkeypatch):
    expire = AsyncMock()
    lapsed = {**DUE, "expires_at": datetime(2026, 1, 1, tzinfo=UTC)}
    monkeypatch.setattr(routines.store, "claim_due", AsyncMock(return_value=[lapsed]))
    monkeypatch.setattr(routines.store, "expire", expire)

    signals = await routines.poll("")

    assert signals == []
    expire.assert_awaited_once_with("r-1")


async def test_one_bad_routine_does_not_lose_the_others(monkeypatch):
    """Same posture as every other source: a poll tick is best-effort per
    item, never all-or-nothing."""
    broken = {**DUE, "id": "r-bad", "cadence": {"nonsense": True}}
    monkeypatch.setattr(
        routines.store, "claim_due", AsyncMock(return_value=[broken, DUE])
    )

    signals = await routines.poll("")

    assert [s.payload["routine_id"] for s in signals] == ["r-1"]


async def test_a_sent_resolution_records_a_spoke_run(monkeypatch):
    record = AsyncMock()
    monkeypatch.setattr(routines.store, "record_run", record)
    signal = (await _one_signal(monkeypatch))

    await routines.record_outcome(signal, "sent")

    assert record.await_args.args[0] == "r-1"
    assert record.await_args.args[1] == "spoke"


async def test_a_vetoed_resolution_records_a_silent_run(monkeypatch):
    """Silence is the routine working correctly, so it must not count as a
    failure or the auto-pause would fire on every healthy routine."""
    record = AsyncMock()
    monkeypatch.setattr(routines.store, "record_run", record)
    signal = await _one_signal(monkeypatch)

    await routines.record_outcome(signal, "vetoed")

    assert record.await_args.args[1] == "silent"


async def test_a_deferred_resolution_records_an_error(monkeypatch):
    record = AsyncMock()
    monkeypatch.setattr(routines.store, "record_run", record)
    signal = await _one_signal(monkeypatch)

    await routines.record_outcome(signal, "deferred")

    assert record.await_args.args[1] == "error"


async def test_a_stale_resolution_records_nothing(monkeypatch):
    """`stale` means the cooldown dropped it before anything ran. Recording a
    run for it would be recording a run that never happened."""
    record = AsyncMock()
    monkeypatch.setattr(routines.store, "record_run", record)
    signal = await _one_signal(monkeypatch)

    await routines.record_outcome(signal, "stale")

    record.assert_not_awaited()


async def test_recording_an_outcome_for_another_source_is_a_no_op(monkeypatch):
    from eve_ambient.types import Signal

    record = AsyncMock()
    monkeypatch.setattr(routines.store, "record_run", record)
    other = Signal(
        source="calendar", key="c-1", occurred_at=datetime.now(UTC),
        member_sub="sub-noah", summary="x",
    )

    await routines.record_outcome(other, "sent")

    record.assert_not_awaited()


async def _one_signal(monkeypatch):
    monkeypatch.setattr(routines.store, "claim_due", AsyncMock(return_value=[DUE]))
    return (await routines.poll(""))[0]
