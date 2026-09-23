"""Resolved coding sessions as signals.

Two deliberate deviations from how other ambient sources behave, both
inherited from sources/computer.py and both for its reasons:

The relevance filter is bypassed (`pipeline` special-cases this source).
Every other signal is a guess about what the family might want to know;
this one was explicitly requested.

`per_member=False`: eve-computer holds no per-member data, so this is
polled once per tick for the whole household - each resolved session's
member comes from Eve's own row, not from the box.

The 24-hour merge is the same re-derivation window computer.py documents: a
signal whose delivery was suppressed (quiet hours, the daily cap) or
deferred (a transient push failure) is re-derived on a later tick instead of
being lost the moment supervisor.tick() stops returning it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from eve.coding import store as coding_store
from eve.coding import supervisor
from eve_ambient.types import Signal

_LOOKBACK = timedelta(hours=24)


def _address_summary(row: dict) -> str:
    """EVE-31. An address session reports on the pull request it answered,
    not on a pull request it opened: it opened none."""
    goal = row["goal"]
    result = row["result"] or {}
    url = result.get("url") or ""
    if row["status"] == "blocked":
        return f"A reviewer asked something I need you for, on {goal}: {result.get('question', '')}".rstrip()
    if row["status"] != "finished":
        return f"I couldn't {goal}. {result.get('error', '')}".rstrip()
    if result.get("error"):
        return f"I worked on {goal}, but not all of it reached GitHub: {result['error']} {url}".strip()
    commits = result.get("commits", 0)
    replies = result.get("replies", 0)
    pushed = f"pushed {commits} commit(s)" if commits else "changed no code"
    return f"I {goal}: {pushed} and replied in {replies} thread(s). {url}".strip()


def _summary(row: dict) -> str:
    if row.get("kind") == "address":
        return _address_summary(row)
    goal = row["goal"]
    result = row["result"] or {}

    if row["status"] == "stale":
        return f"A coding session went stale and never reported back: {goal}"
    if row["status"] == "blocked":
        return f"The coding agent needs an answer on {goal}: {result.get('question', '')}".rstrip()
    if row["status"] == "failed" or result.get("error"):
        return f"A coding session failed: {goal}. {result.get('error', '')}".rstrip()

    prs = [pr for pr in result.get("prs", []) if pr.get("pr_url")]
    if not prs:
        return f"Finished {goal}, but it made no changes, so there's no pull request."
    links = "; ".join(f"{pr['repo']}: {pr['pr_url']}" for pr in prs)
    return f"Finished {goal}. {result.get('summary', '')} Pull requests: {links}".strip()


async def poll(_member_sub: str) -> list[Signal]:
    resolved = await supervisor.tick()
    since = datetime.now(UTC) - _LOOKBACK
    recent = await coding_store.recently_resolved_sessions(since=since)

    by_id: dict[str, dict] = {}
    for row in (*resolved, *recent):
        # `resolved` first: on the tick a session actually transitions its
        # row is fresher than whatever Postgres reads back a moment later,
        # and dict insertion order keeps the first write for a given id.
        by_id.setdefault(row["id"], row)

    return [
        Signal(
            source="coding",
            key=row["id"],
            occurred_at=row["finished_at"],
            member_sub=row["member_sub"],
            summary=_summary(row),
            payload={
                "thread_id": row["thread_id"],
                "goal": row["goal"],
                "repos": row["repos"],
                "result": row["result"],
                "status": row["status"],
            },
            cooldown_hours=24,
        )
        for row in by_id.values()
        # `address` (EVE-31) is reported here rather than in its own source:
        # it is delegated coding work, gated by the same `code.delegate`
        # grant and reported on the thread the original change came from.
        if row.get("kind", "code") in ("code", "address")
    ]
