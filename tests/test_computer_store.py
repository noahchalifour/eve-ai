from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
async def pool(monkeypatch):
    monkeypatch.setenv(
        "EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve"
    )
    from eve.memory import db
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    p = await db.get_pool()
    async with p.connection() as conn:
        await conn.execute("TRUNCATE eve_computer_task")
    yield p
    await db.close_pool()


async def test_create_task_defaults_to_running(pool):
    from eve.computer.store import create_task, get

    await create_task("t1", "sub-noah", "thread-1", "book the flight")
    row = await get("t1")
    assert row["status"] == "running"
    assert row["member_sub"] == "sub-noah"
    assert row["thread_id"] == "thread-1"
    assert row["goal"] == "book the flight"
    assert row["result"] is None


async def test_get_of_an_unknown_task_is_none(pool):
    from eve.computer.store import get

    assert await get("nope") is None


async def test_running_tasks_excludes_finished_ones(pool):
    from eve.computer.store import create_task, mark_finished, running_tasks

    await create_task("t1", "sub-noah", "thread-1", "goal one")
    await create_task("t2", "sub-noah", "thread-2", "goal two")
    await mark_finished("t1", "finished", {"summary": "done"})

    ids = [row["id"] for row in await running_tasks()]
    assert ids == ["t2"]


async def test_mark_finished_records_the_result(pool):
    from eve.computer.store import create_task, get, mark_finished

    await create_task("t1", "sub-noah", "thread-1", "goal")
    await mark_finished("t1", "failed", {"error": "RuntimeError: boom"})
    row = await get("t1")
    assert row["status"] == "failed"
    assert row["result"] == {"error": "RuntimeError: boom"}
    assert row["finished_at"] is not None


async def test_mark_stale_sets_status_and_finished_at(pool):
    from eve.computer.store import create_task, get, mark_stale

    await create_task("t1", "sub-noah", "thread-1", "goal")
    await mark_stale("t1")
    row = await get("t1")
    assert row["status"] == "stale"
    assert row["finished_at"] is not None


async def _finish_at(pool, task_id: str, status: str, finished_at: datetime) -> None:
    """Backdates a task's `finished_at` directly - `mark_finished`/`mark_stale`
    always stamp `now()`, so the `since` filter can't otherwise be exercised
    against a task that resolved before some cutoff."""
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_computer_task SET status = %s, finished_at = %s WHERE id = %s",
            (status, finished_at, task_id),
        )


async def test_recently_resolved_tasks_filters_by_since_and_status(pool):
    from eve.computer.store import create_task, recently_resolved_tasks

    now = datetime.now(UTC)
    old = now - timedelta(hours=48)
    recent = now - timedelta(hours=1)

    await create_task("old-finished", "sub-noah", "thread-1", "goal old")
    await _finish_at(pool, "old-finished", "finished", old)

    await create_task("recent-finished", "sub-noah", "thread-2", "goal recent")
    await _finish_at(pool, "recent-finished", "finished", recent)

    await create_task("recent-failed", "sub-noah", "thread-3", "goal failed")
    await _finish_at(pool, "recent-failed", "failed", recent)

    await create_task("recent-stale", "sub-noah", "thread-4", "goal stale")
    await _finish_at(pool, "recent-stale", "stale", recent)

    await create_task("still-running", "sub-noah", "thread-5", "goal running")

    since = now - timedelta(hours=24)
    rows = await recently_resolved_tasks(since=since)
    ids = {row["id"] for row in rows}

    # Older than `since`: excluded despite being resolved.
    assert "old-finished" not in ids
    # Never resolved: excluded regardless of `since`.
    assert "still-running" not in ids
    # Resolved within the window, any terminal status: included.
    assert ids == {"recent-finished", "recent-failed", "recent-stale"}


async def test_recently_resolved_tasks_orders_by_finished_at(pool):
    from eve.computer.store import create_task, recently_resolved_tasks

    now = datetime.now(UTC)
    await create_task("second", "sub-noah", "thread-1", "goal")
    await _finish_at(pool, "second", "finished", now - timedelta(minutes=1))
    await create_task("first", "sub-noah", "thread-2", "goal")
    await _finish_at(pool, "first", "finished", now - timedelta(minutes=10))

    rows = await recently_resolved_tasks(since=now - timedelta(hours=24))
    assert [row["id"] for row in rows] == ["first", "second"]
