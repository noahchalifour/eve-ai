"""Per-thread registry for background memory extraction.

`extract` hands its work here and returns, so a turn ends when Eve stops
talking rather than when her bookkeeping finishes. The next turn on the same
thread joins the pending task before it reads memory, which is what keeps
"detached" from meaning "eventually consistent": the wait lands in the gap
where a member is typing, not in front of the reply they just asked for.

This module imports nothing from the rest of `eve`, so both `extract` (which
spawns) and `recall` (which joins) may depend on it without a cycle.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# thread_id -> the newest extraction still in flight for that thread.
_pending: dict[str, asyncio.Task[None]] = {}
# Tasks not reachable by thread id: those spawned without one, and older
# tasks displaced by a second spawn on the same thread. Held for one reason
# only - asyncio keeps just a WEAK reference to a running task, so a task
# nobody else references can be garbage-collected mid-flight and lose the
# writes it was about to make.
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

    previous = _pending.get(thread_id)
    if previous is not None and not previous.done():
        # Two runs raced on one thread (Aegra does not serialize them), or a
        # join timed out and the turn moved on. Only the newest is joinable
        # by thread id; the older one still needs a reference to survive.
        _hold(previous)

    _pending[thread_id] = task

    def _release(finished: asyncio.Task[None]) -> None:
        # Only clear the slot if it is still OURS. A later spawn may have
        # replaced it, and popping unconditionally would drop a live task's
        # only strong reference.
        if _pending.get(thread_id) is finished:
            del _pending[thread_id]

    task.add_done_callback(_release)
    return task


async def join(thread_id: str | None, budget_s: float) -> bool:
    """Wait for this thread's pending extraction.

    Returns True if there was nothing to wait for or it finished (including
    by failing - a failed extraction is still finished), False if the budget
    ran out. The budget bounds the WAIT, not the work: the task is shielded,
    so giving up on it does not cancel the writes it is partway through.
    """
    task = _pending.get(thread_id) if thread_id else None
    if task is None or task.done():
        return True
    try:
        await asyncio.wait_for(asyncio.shield(task), budget_s)
    except TimeoutError:
        return False
    except Exception:
        # Logged by _log_failure. A broken extraction must not break the
        # turn that merely waited for it.
        return True
    return True


async def drain() -> None:
    """Await every in-flight extraction. For tests and orderly shutdown."""
    tasks = [*_pending.values(), *_detached]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def clear() -> None:
    """Drop every reference without awaiting. Test isolation only."""
    _pending.clear()
    _detached.clear()
