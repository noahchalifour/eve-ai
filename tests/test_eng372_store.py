"""Storage added for ENG-372 against real Postgres: routine `kind` and the
record store's keyed delete."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def pool(monkeypatch):
    monkeypatch.setenv("EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve")
    from eve.memory import db
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    p = await db.get_pool()
    async with p.connection() as conn:
        await conn.execute("DELETE FROM eve_routine")
        await conn.execute("TRUNCATE eve_record")
    yield p
    async with p.connection() as conn:
        await conn.execute("DELETE FROM eve_routine")
    await db.close_pool()


async def _routine(kind="routine", cadence=None, when=None):
    from eve.routines import store

    return await store.create(
        member_sub="sub-noah", title=f"{kind} one", instruction="do it",
        cadence=cadence or {"daily_at": "08:00"}, timezone="UTC",
        next_run_at=when or datetime.now(UTC) + timedelta(hours=1), expires_at=None, kind=kind,
    )


async def test_existing_rows_read_as_routines_and_kind_filters():
    from eve.routines import store

    await _routine()
    await _routine("reminder", {"once_at": "2030-01-01T00:00:00+00:00"})
    assert {r["kind"] for r in await store.list_for("sub-noah")} == {"routine", "reminder"}
    assert [r["kind"] for r in await store.list_for("sub-noah", kind="reminder")] == ["reminder"]
    assert [r["kind"] for r in await store.find_by_title("sub-noah", "one", kind="routine")] == ["routine"]
    with pytest.raises(ValueError):
        await _routine("bogus")


async def test_a_due_reminder_fires_once_and_is_retired():
    from eve.routines import store
    from eve_ambient.sources import routines

    past = datetime.now(UTC) - timedelta(minutes=1)
    row = await _routine("reminder", {"once_at": past.isoformat()}, when=past)
    [signal] = await routines.poll("")
    assert signal.payload["kind"] == "reminder"
    # Leased, so the next tick finds nothing.
    assert await routines.poll("") == []
    await routines.record_outcome(signal, "sent")
    assert (await store.get("sub-noah", row["id"]))["status"] == "expired"


async def test_record_keyed_delete_and_clear():
    from eve.records import store

    for item in ("milk", "eggs"):
        await store.append("household:shared", "list.groceries", {"item": item}, key=item)
    await store.append("sub-noah", "list.groceries", {"item": "milk"}, key="milk")
    assert await store.delete_keys("household:shared", "list.groceries", ["milk", "bread"]) == ["milk"]
    assert [r["key"] for r in await store.query("household:shared", "list.groceries")] == ["eggs"]
    # Another owner's identically-keyed row is untouched.
    assert [r["key"] for r in await store.query("sub-noah", "list.groceries")] == ["milk"]
    assert await store.collections_with_prefix("household:shared", "list.") == ["list.groceries"]
    assert await store.clear("household:shared", "list.groceries") == 1
    assert await store.query("household:shared", "list.groceries") == []
