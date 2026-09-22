"""Quiet-period debouncing for webhook-triggered sessions (EVE-32, EVE-31).

WHY. A member pushing ten commits in a row, or a reviewer leaving a review
with ten inline comments, sends ten webhooks. Each one could start a full ACP
session holding a scarce slot and spending a real coding model's tokens. A
burst should produce one session, started once the pull request has gone
quiet, carrying the newest state.

WHY IN MEMORY. eve-ambient is one replica by design (see app.py), and a
pending action is only ever a few minutes of sleep. A restart drops what is
pending, which costs a re-review or a follow-up that the next event on the
pull request (or a human relabelling it) re-creates. Persisting a timer to
Postgres to save that would be a bound with no reason behind it.

ONCE AN ACTION FIRES IT IS NO LONGER PENDING. A new event during a running
action schedules a fresh one rather than cancelling the dispatch midway,
which could leave a session on the box with no row in Eve's database.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Hashable

logger = logging.getLogger(__name__)


class Debouncer:
    def __init__(self) -> None:
        self._pending: dict[Hashable, asyncio.Task] = {}

    def __contains__(self, key: Hashable) -> bool:
        return key in self._pending

    def __len__(self) -> int:
        return len(self._pending)

    def schedule(
        self, key: Hashable, delay: float, action: Callable[[], Awaitable[object]]
    ) -> None:
        """Run `action` after `delay` seconds without another `schedule` for
        the same key. A later call replaces both the timer and the action, so
        what finally runs is built from the newest event."""
        existing = self._pending.pop(key, None)
        if existing is not None:
            existing.cancel()
        self._pending[key] = asyncio.create_task(self._fire(key, delay, action))

    async def _fire(
        self, key: Hashable, delay: float, action: Callable[[], Awaitable[object]]
    ) -> None:
        await asyncio.sleep(delay)
        if self._pending.get(key) is asyncio.current_task():
            del self._pending[key]
        try:
            await action()
        except Exception:
            logger.warning("debounced action for %r failed", key, exc_info=True)

    async def cancel_all(self) -> None:
        tasks = list(self._pending.values())
        self._pending.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
