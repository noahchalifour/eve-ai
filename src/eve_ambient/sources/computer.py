"""Finished computer tasks as signals. The relevance filter is bypassed for
this source (design doc: "Reporting back") - `eve_ambient.pipeline` special-
cases `source == "computer"` instead of calling the REFLEX filter, since a
task a member explicitly asked for is never "not relevant."

`per_member=False`: eve-computer holds no per-member data (design doc: "The
box learns nothing about the family"), so this is polled once per tick for
the whole household, like `finances`, not once per member holding the
permission - each finished task's member comes from Eve's own task row.

`poll()` merges `poller.sync()`'s freshly-resolved rows (this tick's
transitions) with `store.recently_resolved_tasks` over a 24-hour window, so
a task whose signal delivery was suppressed (quiet hours, the daily cap) or
deferred (a transient `notify.deliver` failure) gets re-derived on a later
tick instead of being lost the moment `poller.sync()` stops returning it -
the same way every other polled source re-derives from live upstream state
each tick rather than only on the tick something changed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from eve.computer import poller
from eve.computer import store as computer_store
from eve_ambient.types import Signal

_LOOKBACK = timedelta(hours=24)


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
    since = datetime.now(UTC) - _LOOKBACK
    recent = await computer_store.recently_resolved_tasks(since=since)

    by_id: dict[str, dict] = {}
    for task in (*resolved, *recent):
        # `resolved` first: on the tick a task actually transitions, its row
        # comes from `poller.sync()`'s in-memory dict (fresher than whatever
        # `recently_resolved_tasks` reads back from Postgres a moment
        # later), and dict insertion order keeps the first write for a given
        # id.
        by_id.setdefault(task["id"], task)

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
            # Not "never recurs" - a task id resolves once, but its signal
            # can still be re-derived (above) for up to 24 hours if delivery
            # was suppressed or deferred the first time around. This is that
            # same 24-hour retry window, not a claim that the task itself
            # changes state again.
            cooldown_hours=24,
        )
        for task in by_id.values()
    ]
