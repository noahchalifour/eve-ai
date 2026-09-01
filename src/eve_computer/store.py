"""In-memory task state for the box. Not durable across a restart -
eve-ambient's poller (eve.computer.poller) marks a task stale after a
timeout when the box stops answering for it, rather than this service
needing to survive its own restart to report a result.

ponytail: a dict behind a lock, not sqlite - one task runs at a time and
nothing here needs to outlive a restart. Move to sqlite on the PVC if a
future requirement needs task history to survive one.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class Task:
    id: str
    goal: str
    status: str = "queued"  # queued -> running -> finished | failed | killed
    result: dict | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


_tasks: dict[str, Task] = {}
_lock = asyncio.Lock()


async def create(task_id: str, goal: str) -> Task:
    async with _lock:
        task = Task(id=task_id, goal=goal)
        _tasks[task_id] = task
        return task


async def get(task_id: str) -> Task | None:
    return _tasks.get(task_id)


async def set_status(task_id: str, status: str) -> None:
    async with _lock:
        if task_id in _tasks:
            _tasks[task_id].status = status


async def set_result(task_id: str, status: str, result: dict) -> None:
    async with _lock:
        if task_id in _tasks:
            _tasks[task_id].status = status
            _tasks[task_id].result = result
