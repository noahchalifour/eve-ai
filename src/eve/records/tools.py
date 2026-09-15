"""Two tools, no domains.

Every per-domain logging tool anyone might want - log a workout, log a book,
log a chore - is this one tool with a different `collection` string. What
each domain MEANS lives in a skill, which is prose and costs no code.

`known_fields` comes back on every append so the model can match the shape
it used last time. It is advisory: a divergent payload is stored, not
rejected, because member-recorded data must never be lost to a schema
disagreement it cannot see.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from eve.records import store

logger = logging.getLogger(__name__)

MAX_DAYS = 3650


def _member(config: RunnableConfig) -> str:
    return config["configurable"]["member"]["sub"]


@tool
async def record_append(
    collection: str,
    payload: dict,
    config: RunnableConfig,
    occurred_at: str | None = None,
    key: str | None = None,
) -> str:
    """Record one entry the member wants kept and later charted.

    `collection` is a stable dotted name you choose for this kind of entry
    (for example `reading.session`). Reuse the SAME name and the same payload
    keys for the same kind of thing, or a chart over it will miss entries.
    `occurred_at` is an ISO-8601 timestamp; omit it for now.
    """
    member_sub = _member(config)
    try:
        when = datetime.fromisoformat(occurred_at) if occurred_at else None
    except ValueError:
        return f"error: occurred_at must be ISO-8601, got {occurred_at!r}"

    try:
        result = await store.append(
            member_sub, collection, payload, occurred_at=when, key=key
        )
    except Exception as exc:
        logger.warning("record_append failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"

    try:
        fields = await store.known_fields(member_sub, collection)
    except Exception as exc:
        # The field list is advisory: the entry is already stored, so a
        # failure here must not read as a failed append, or a keyless retry
        # would duplicate the row. Log it and continue as if unknown.
        logger.warning(
            "record_append stored the entry but known_fields failed",
            exc_info=True,
        )
        fields = []

    note = "already recorded" if result["deduped"] else "recorded"
    if fields:
        return (
            f"{note} in {collection}. Fields used in this collection so far: "
            f"{', '.join(fields)}. Reuse these names for consistency."
        )
    return f"{note} in {collection}. This is the first entry in it."


@tool
async def record_query(
    collection: str,
    config: RunnableConfig,
    days: int = 30,
) -> str:
    """Read back this member's recorded entries in one collection."""
    member_sub = _member(config)
    window = max(1, min(days, MAX_DAYS))
    since = datetime.now(timezone.utc) - timedelta(days=window)

    try:
        rows = await store.query(member_sub, collection, since=since)
    except Exception as exc:
        logger.warning("record_query failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"

    if not rows:
        return (
            f"Nothing recorded in {collection} in the last {window} days. "
            "Check the collection name if you expected entries."
        )
    lines = [
        f"- {row['occurred_at']}: {json.dumps(row['payload'], default=str)}"
        for row in rows
    ]
    return "\n".join(lines)