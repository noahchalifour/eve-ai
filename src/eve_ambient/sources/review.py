"""Resolved review sessions as signals.

Mirrors sources/coding.py, including the 24-hour re-derivation window: a
signal whose delivery was suppressed or deferred is re-derived on a later
tick rather than lost.

`per_member=False` for coding.py's reason: the rows are household-wide and
each carries its own member.

The supervisor's own tick already drives these sessions (app.py's
`_supervise_forever`), so unlike sources/coding.py this one only reads. It
does not call `supervisor.tick()` a second time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from eve.coding import store as coding_store
from eve_ambient.types import Signal

_LOOKBACK = timedelta(hours=24)


def _summary(row: dict) -> str:
    goal = row["goal"]
    result = row["result"] or {}

    if row["status"] != "finished":
        return f"A review failed: {goal}. {result.get('error', '')}".rstrip()

    findings = result.get("findings", 0)
    counts = result.get("counts") or {}
    tally = ", ".join(f"{n} {severity}" for severity, n in counts.items())
    url = result.get("url") or ""

    if not result.get("posted"):
        return (
            f"I reviewed {goal} and found {findings} thing(s) ({tally}), but the "
            f"review could not be posted to GitHub: {result.get('error', '')}"
        ).rstrip()
    if findings == 0:
        return f"I reviewed {goal} and found no findings. {url}".strip()
    return f"I reviewed {goal}: {findings} finding(s), {tally}. {url}".strip()


async def poll(_member_sub: str) -> list[Signal]:
    since = datetime.now(UTC) - _LOOKBACK
    rows = await coding_store.recently_resolved_sessions(since=since)

    return [
        Signal(
            source="review",
            key=f"{row['id']}:resolved",
            occurred_at=row["finished_at"],
            member_sub=row["member_sub"],
            summary=_summary(row),
            payload={
                "thread_id": row["thread_id"],
                "goal": row["goal"],
                "repos": row["repos"],
                "pr_number": row.get("pr_number"),
                "result": row["result"],
                "status": row["status"],
            },
            cooldown_hours=24,
        )
        for row in rows
        if row.get("kind") == "review"
    ]
