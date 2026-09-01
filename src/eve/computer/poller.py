"""The poller state machine: for every task Eve is still waiting on, ask the
box once, and update Eve's own row accordingly.

Kept separate from `eve_ambient.sources.computer` so it can be unit-tested
with only eve-computer (via `eve.tools_client.get_computer_task`) mocked, not
the whole ambient gate chain - "the poller state machine" and "the ambient
source" are two of the four things the design doc's testing section names
as separately covered by the unit tier.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from eve import tools_client
from eve.computer import store
from eve.settings import get_settings

logger = logging.getLogger(__name__)


async def sync(now: datetime | None = None) -> list[dict]:
    """Returns the rows that resolved - finished, failed, or went stale - on
    this tick. `eve_ambient.sources.computer.poll` turns each into a Signal."""
    now = now or datetime.now(UTC)
    stale_after = timedelta(minutes=get_settings().computer_task_stale_minutes)
    resolved: list[dict] = []

    for task in await store.running_tasks():
        try:
            status = await tools_client.get_computer_task(task["id"])
        except Exception:
            # get_computer_task already degrades every failure to None; this
            # guards a future regression that makes it raise instead, so one
            # bad task cannot stop every other task from being checked.
            logger.warning("checking on task %s raised", task["id"], exc_info=True)
            status = None

        if status is None:
            if now - task["updated_at"] > stale_after:
                await store.mark_stale(task["id"])
                resolved.append({**task, "status": "stale", "result": None, "finished_at": now})
            continue

        box_status = status.get("status")
        if box_status not in ("finished", "failed"):
            continue

        result = status.get("result") or {}
        outcome = "failed" if box_status == "failed" or result.get("error") else "finished"
        await store.mark_finished(task["id"], outcome, result)
        resolved.append({**task, "status": outcome, "result": result, "finished_at": now})

    return resolved
