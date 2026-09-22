"""Ownership is enforced here or not at all: Aegra's @auth.on handlers do not
reach a custom route, so every statement carries member_sub."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

CADENCE = {"daily_at": "08:00"}


async def _make(member_sub="sub-noah", *, next_run_at=None, title="Flights"):
    from eve.routines import store

    return await store.create(
        member_sub=member_sub,
        title=title,
        instruction="Check Aeroplan fares YVR to SJD.",
        cadence=CADENCE,
        timezone="America/Vancouver",
        next_run_at=next_run_at or datetime.now(UTC) + timedelta(hours=1),
        expires_at=None,
    )


@pytest.fixture(autouse=True)
async def _clean():
    from eve.memory.db import get_pool

    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM eve_routine")
    yield
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM eve_routine")


async def test_a_created_routine_comes_back_with_an_id_and_revision_one():
    row = await _make()

    assert row["id"]
    assert row["revision"] == 1
    assert row["status"] == "active"
    assert row["consecutive_failures"] == 0


async def test_listing_returns_only_this_members_routines():
    from eve.routines import store

    await _make("sub-noah")
    await _make("sub-kendra")

    rows = await store.list_for("sub-noah")

    assert len(rows) == 1
    assert rows[0]["member_sub"] == "sub-noah"


async def test_getting_another_members_routine_returns_none():
    """Absent and foreign answer identically, so probing learns nothing."""
    from eve.routines import store

    row = await _make("sub-kendra")

    assert await store.get("sub-noah", row["id"]) is None


async def test_deleting_another_members_routine_reports_false():
    from eve.routines import store

    row = await _make("sub-kendra")

    assert await store.delete("sub-noah", row["id"]) is False
    assert await store.get("sub-kendra", row["id"]) is not None


async def test_find_by_title_matches_case_insensitively_on_a_substring():
    from eve.routines import store

    await _make(title="Aeroplan flights to Los Cabos")

    found = await store.find_by_title("sub-noah", "flights")

    assert len(found) == 1


async def test_updating_bumps_the_revision():
    from eve.routines import store

    row = await _make()

    updated = await store.update(
        "sub-noah", row["id"], row["revision"], status="paused"
    )

    assert updated["status"] == "paused"
    assert updated["revision"] == row["revision"] + 1


async def test_updating_at_a_stale_revision_returns_none():
    """The guard is in the UPDATE's own WHERE clause, not a read-then-write,
    so two concurrent writers cannot both pass it."""
    from eve.routines import store

    row = await _make()
    await store.update("sub-noah", row["id"], row["revision"], title="First")

    assert await store.update(
        "sub-noah", row["id"], row["revision"], title="Second"
    ) is None


async def test_claim_due_returns_only_active_routines_that_are_due():
    from eve.routines import store

    past = datetime.now(UTC) - timedelta(minutes=5)
    future = datetime.now(UTC) + timedelta(hours=5)
    due = await _make(next_run_at=past, title="Due")
    await _make(next_run_at=future, title="Later")
    paused = await _make(next_run_at=past, title="Paused")
    await store.update("sub-noah", paused["id"], paused["revision"], status="paused")

    claimed = await store.claim_due(datetime.now(UTC))

    assert [row["id"] for row in claimed] == [due["id"]]


async def test_claim_due_advances_next_run_at_so_a_second_claim_finds_nothing():
    """The whole reason next_run_at is stored: a compose turn still running
    when the next tick arrives must not re-fire the same occurrence.

    This test pins a bug that was live in the first draft of this plan.
    Writing `SET next_run_at = now()` reads as "claimed" and is not: `now()`
    is the transaction timestamp and the predicate is `next_run_at <= now()`,
    so the row is still due the moment the statement commits and the next
    tick claims it again. The claim writes a LEASE instead.
    """
    from eve.routines import store

    await _make(next_run_at=datetime.now(UTC) - timedelta(minutes=5))

    first = await store.claim_due(datetime.now(UTC))
    second = await store.claim_due(datetime.now(UTC))

    assert len(first) == 1
    assert second == []


async def test_a_claim_leases_the_row_rather_than_releasing_it():
    """A worker that dies between claiming and scheduling must leave the
    firing retriable, not lost. The lease expiring is what retries it."""
    from eve.routines import store

    await _make(next_run_at=datetime.now(UTC) - timedelta(minutes=5))

    claimed = await store.claim_due(datetime.now(UTC))
    row = await store.get("sub-noah", claimed[0]["id"])

    assert row["next_run_at"] > datetime.now(UTC)
    assert row["next_run_at"] <= datetime.now(UTC) + timedelta(
        minutes=store.CLAIM_LEASE_MINUTES + 1
    )


async def test_claim_due_carries_the_scheduled_time_it_claimed():
    """The signal key is <id>:<scheduled_time>, so the claimed occurrence has
    to come back with the row."""
    from eve.routines import store

    when = datetime.now(UTC) - timedelta(minutes=5)
    await _make(next_run_at=when)

    claimed = await store.claim_due(datetime.now(UTC))

    assert claimed[0]["scheduled_for"] == when


async def test_recording_a_spoke_run_resets_the_failure_counter():
    from eve.routines import store

    row = await _make()
    await store.record_run(row["id"], "error", failure_limit=5)
    await store.record_run(row["id"], "spoke", failure_limit=5)

    fresh = await store.get("sub-noah", row["id"])

    assert fresh["consecutive_failures"] == 0
    assert fresh["last_outcome"] == "spoke"
    assert fresh["last_run_at"] is not None


async def test_a_silent_run_is_a_success_not_a_failure():
    """Silence is the routine working. Counting a veto as a failure would
    auto-pause every well-behaved routine within a week."""
    from eve.routines import store

    row = await _make()
    await store.record_run(row["id"], "error", failure_limit=5)
    await store.record_run(row["id"], "silent", failure_limit=5)

    fresh = await store.get("sub-noah", row["id"])

    assert fresh["consecutive_failures"] == 0
    assert fresh["status"] == "active"


async def test_reaching_the_failure_limit_pauses_the_routine():
    from eve.routines import store

    row = await _make()
    for _ in range(5):
        await store.record_run(row["id"], "error", failure_limit=5)

    fresh = await store.get("sub-noah", row["id"])

    assert fresh["consecutive_failures"] == 5
    assert fresh["status"] == "paused"


async def test_expiring_moves_the_routine_out_of_the_due_query():
    from eve.routines import store

    row = await _make(next_run_at=datetime.now(UTC) - timedelta(minutes=5))
    await store.expire(row["id"])

    assert await store.claim_due(datetime.now(UTC)) == []
    assert (await store.get("sub-noah", row["id"]))["status"] == "expired"
