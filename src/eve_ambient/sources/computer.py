"""Finished computer tasks as signals. The relevance filter is bypassed for
this source (design doc: "Reporting back") - `eve_ambient.pipeline` special-
cases `source == "computer"` instead of calling the REFLEX filter, since a
task a member explicitly asked for is never "not relevant."

`per_member=False`: eve-computer holds no per-member data (design doc: "The
box learns nothing about the family"), so this is polled once per tick for
the whole household, like `finances`, not once per member holding the
permission - each finished task's member comes from Eve's own task row.
"""

from __future__ import annotations

from eve.computer import poller
from eve_ambient.types import Signal


def _summary(task: dict) -> str:
    goal = task["goal"]
    if task["status"] == "stale":
        return f"A computer task went stale and never reported back: {goal}"
    result = task["result"] or {}
    if task["status"] == "failed" or result.get("error"):
        return f"A computer task failed: {goal}. {result.get('error', '')}".rstrip()
    return f"Finished a computer task: {goal}"


async def poll(_member_sub: str) -> list[Signal]:
    resolved = await poller.sync()
    return [
        Signal(
            source="computer",
            key=task["id"],
            occurred_at=task["finished_at"],
            member_sub=task["member_sub"],
            summary=_summary(task),
            payload={
                "thread_id": task["thread_id"],
                "goal": task["goal"],
                "result": task["result"],
                "status": task["status"],
            },
            # A task id never recurs - it resolves exactly once, so there is
            # no cooldown window for it to re-fire within.
            cooldown_hours=0,
        )
        for task in resolved
    ]
