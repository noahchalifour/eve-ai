"""Calendar events as signals, via eve-tools' CalDAV client.

Two signal shapes per event, and they answer different questions. The `start`
key answers "this is about to happen"; the `rev` key answers "this changed."
Both are content-keyed, so neither needs a stored cursor: a start time only
enters the window once, and a revision only appears once per edit.
"""

from __future__ import annotations

from datetime import UTC, datetime

from eve.settings import get_settings
from eve.tools_client import invoke
from eve_ambient.types import Signal, tool_result


def _occurred_at(start: str | None) -> datetime:
    try:
        return datetime.fromisoformat(str(start))
    except (TypeError, ValueError):
        return datetime.now(UTC)


async def poll(member_sub: str) -> list[Signal]:
    lookahead = get_settings().ambient_calendar_lookahead_minutes
    result = tool_result(
        await invoke(
            "calendar.list_events",
            {"member_sub": member_sub, "lookahead_minutes": lookahead},
        )
    )
    if result is None:
        return []

    signals: list[Signal] = []
    for event in result.get("events") or []:
        uid = str(event.get("uid") or "")
        if not uid:
            continue
        title = event.get("summary") or "(untitled event)"
        where = f" at {event['location']}" if event.get("location") else ""
        start = event.get("start")
        occurred = _occurred_at(start)
        signals.append(
            Signal(
                source="calendar",
                key=f"{uid}:start:{start}",
                occurred_at=occurred,
                member_sub=member_sub,
                summary=f"Upcoming: {title}{where}, starting {start}.",
                payload=event,
            )
        )
        revision = event.get("revision")
        if revision:
            signals.append(
                Signal(
                    source="calendar",
                    key=f"{uid}:rev:{revision}",
                    occurred_at=occurred,
                    member_sub=member_sub,
                    summary=f"Calendar entry changed: {title}{where}, now starting {start}.",
                    payload=event,
                )
            )
    return signals
