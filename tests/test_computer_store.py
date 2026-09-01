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
