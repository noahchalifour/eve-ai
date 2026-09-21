"""One routine, from creation to a firing that speaks and a firing that does
not. Exercises the real store against the real schema."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _clean():
    from eve.memory.db import get_pool

    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM eve_routine")
    yield


async def test_a_routine_fires_once_then_waits_for_its_next_slot():
    from eve.routines import store
    from eve_ambient.sources import routines

    await store.create(
        member_sub="sub-noah",
        title="Flights",
        instruction="Check Aeroplan fares YVR to SJD.",
        cadence={"every_hours": 6},
        timezone="America/Vancouver",
        next_run_at=datetime.now(UTC) - timedelta(minutes=1),
        expires_at=None,
    )

    first = await routines.poll("")
    second = await routines.poll("")

    assert len(first) == 1
    assert second == []

    row = (await store.list_for("sub-noah"))[0]
    assert row["next_run_at"] > datetime.now(UTC)


async def test_a_silent_firing_keeps_the_routine_healthy():
    from eve.routines import store
    from eve_ambient.sources import routines

    await store.create(
        member_sub="sub-noah", title="Flights", instruction="Check fares.",
        cadence={"every_hours": 6}, timezone="America/Vancouver",
        next_run_at=datetime.now(UTC) - timedelta(minutes=1), expires_at=None,
    )
    signal = (await routines.poll(""))[0]

    await routines.record_outcome(signal, "vetoed")

    row = (await store.list_for("sub-noah"))[0]
    assert row["last_outcome"] == "silent"
    assert row["consecutive_failures"] == 0
    assert row["status"] == "active"


async def test_repeated_infrastructure_failure_pauses_the_routine():
    from eve.routines import store
    from eve.settings import get_settings
    from eve_ambient.sources import routines

    await store.create(
        member_sub="sub-noah", title="Flights", instruction="Check fares.",
        cadence={"every_hours": 1}, timezone="America/Vancouver",
        next_run_at=datetime.now(UTC) - timedelta(minutes=1), expires_at=None,
    )

    for _ in range(get_settings().routine_failure_limit):
        row = (await store.list_for("sub-noah"))[0]
        await store.schedule_next(row["id"], datetime.now(UTC) - timedelta(minutes=1))
        signal = (await routines.poll(""))[0]
        await routines.record_outcome(signal, "deferred")

    row = (await store.list_for("sub-noah"))[0]
    assert row["status"] == "paused"

    # A paused routine is out of the due query even when its time has passed.
    await store.schedule_next(row["id"], datetime.now(UTC) - timedelta(minutes=1))
    assert await routines.poll("") == []


async def test_an_expired_routine_retires_instead_of_firing():
    from eve.routines import store
    from eve_ambient.sources import routines

    await store.create(
        member_sub="sub-noah", title="February flights", instruction="Check fares.",
        cadence={"every_hours": 6}, timezone="America/Vancouver",
        next_run_at=datetime.now(UTC) - timedelta(minutes=1),
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )

    assert await routines.poll("") == []
    assert (await store.list_for("sub-noah"))[0]["status"] == "expired"
