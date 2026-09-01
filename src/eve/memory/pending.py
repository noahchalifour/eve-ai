"""Per-thread registry for background memory extraction.

`extract` hands its work here and returns, so a turn ends when Eve stops
talking rather than when her bookkeeping finishes. The next turn on the same
thread joins the pending task(s) before it reads memory, which is what keeps
"detached" from meaning "eventually consistent": the wait lands in the gap
where a member is typing, not in front of the reply they just asked for.

This guarantee is process-local: `_pending` and `_detached` are ordinary
module-global Python state, not shared across processes. A second replica of
`eve`, or a rolling deploy that moves turn N+1 to a fresh process, has
nothing in `_pending` to find and `join` returns EMPTY - not because there
was nothing pending, but because this process never knew about it. See ADR
0010's Consequences section.

This module imports nothing from the rest of `eve`, so both `extract` (which
spawns) and `recall` (which joins) may depend on it without a cycle.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class JoinResult(str, Enum):
    """What `join` actually did, so a span attribute can tell these apart.

    A bare bool collapses "there was nothing pending" and "something was
    pending and finished" into the same True - which means a multi-replica
    deployment silently serving stale reads (nothing pending IN THIS
    PROCESS) reads identically to a healthy single-process join.
    """

    JOINED = "joined"
    EMPTY = "empty"
    TIMEOUT = "timeout"


# thread_id -> every extraction still in flight for that thread. Usually a
# set of one, but two runs can race on the same thread (Aegra does not
# serialize them), or a join can time out and the turn move on while the
# old task keeps running - either way, ALL of them must be joinable, not
# just the newest.
_pending: dict[str, set[asyncio.Task[None]]] = {}
# Tasks not reachable by thread id: those spawned without one. Held for one
# reason only - asyncio keeps just a WEAK reference to a running task, so a
# task nobody else references can be garbage-collected mid-flight and lose
# the writes it was about to make.
_detached: set[asyncio.Task[None]] = set()


def _log_failure(task: asyncio.Task[None]) -> None:
    """Consume the exception so asyncio does not report it as never-retrieved.

    Extraction already swallows its own failures; this catches anything that
    escapes the coroutine entirely, which would otherwise surface only as a
    "Task exception was never retrieved" line at GC time.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("background extraction failed", exc_info=exc)


def _hold(task: asyncio.Task[None]) -> None:
    _detached.add(task)
    task.add_done_callback(_detached.discard)


def spawn(thread_id: str | None, coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
    """Run `coro` in the background, keyed by thread so `join` can find it."""
    task = asyncio.create_task(coro)
    task.add_done_callback(_log_failure)

    if thread_id is None:
        _hold(task)
        return task

    _pending.setdefault(thread_id, set()).add(task)

    def _release(finished: asyncio.Task[None]) -> None:
        # Discard from whatever bucket is current, and drop the bucket
        # itself once empty so a stale key does not accumulate forever.
        bucket = _pending.get(thread_id)
        if bucket is None:
            return
        bucket.discard(finished)
        if not bucket:
            del _pending[thread_id]

    task.add_done_callback(_release)
    return task


async def join(thread_id: str | None, budget_s: float) -> JoinResult:
    """Wait for every extraction still pending on this thread.

    Returns EMPTY if there was nothing to wait for, JOINED if everything
    pending finished within the budget (including by failing - a failed
    extraction is still finished), TIMEOUT if the budget ran out. The budget
    bounds the WAIT, not the work: the gather is shielded, so giving up on it
    does not cancel the writes any task is partway through.
    """
    tasks = _pending.get(thread_id) if thread_id else None
    if not tasks:
        return JoinResult.EMPTY
    try:
        await asyncio.wait_for(
            asyncio.shield(asyncio.gather(*tasks, return_exceptions=True)),
            budget_s,
        )
    except TimeoutError:
        return JoinResult.TIMEOUT
    return JoinResult.JOINED


async def drain() -> None:
    """Await every in-flight extraction. For tests and orderly shutdown."""
    tasks = [task for bucket in _pending.values() for task in bucket] + [*_detached]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def clear() -> None:
    """Drop every reference without awaiting. Test isolation only."""
    _pending.clear()
    _detached.clear()
