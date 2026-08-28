import asyncio

import pytest

from eve.memory import pending


@pytest.fixture(autouse=True)
def _clean_registry():
    pending.clear()
    yield
    pending.clear()


async def test_spawn_runs_the_coroutine_in_the_background():
    ran = asyncio.Event()

    async def work():
        ran.set()

    pending.spawn("t1", work())
    assert not ran.is_set()  # not yet - spawn must not await
    await pending.drain()
    assert ran.is_set()


async def test_join_waits_for_the_pending_task():
    order = []

    async def work():
        await asyncio.sleep(0.01)
        order.append("extraction")

    pending.spawn("t1", work())
    assert await pending.join("t1", 1.0) is True
    order.append("recall")
    assert order == ["extraction", "recall"]


async def test_join_with_nothing_pending_returns_immediately():
    assert await pending.join("t1", 1.0) is True
    assert await pending.join(None, 1.0) is True


async def test_join_gives_up_at_the_budget_without_killing_the_task():
    """The budget bounds the WAIT, not the work. A slow extraction must still
    land - it is the next turn's patience that ran out, not the writes."""
    finished = asyncio.Event()

    async def slow():
        await asyncio.sleep(0.05)
        finished.set()

    task = pending.spawn("t1", slow())
    assert await pending.join("t1", 0.005) is False
    assert not task.cancelled()
    await pending.drain()
    assert finished.is_set()


async def test_a_failing_task_does_not_raise_into_join():
    async def boom():
        raise RuntimeError("gemini is down")

    pending.spawn("t1", boom())
    assert await pending.join("t1", 1.0) is True


async def test_a_second_spawn_does_not_strand_the_first():
    """Two runs can overlap on one thread - Aegra does not prevent it. The
    older task must keep a strong reference or the GC may take it mid-write."""
    done = []

    async def work(name):
        await asyncio.sleep(0.01)
        done.append(name)

    pending.spawn("t1", work("first"))
    pending.spawn("t1", work("second"))
    await pending.drain()
    assert sorted(done) == ["first", "second"]


async def test_an_anonymous_task_is_still_awaited_by_drain():
    ran = asyncio.Event()

    async def work():
        ran.set()

    pending.spawn(None, work())
    await pending.drain()
    assert ran.is_set()
