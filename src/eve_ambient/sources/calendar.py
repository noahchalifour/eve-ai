"""Calendar events as signals, via eve-tools' CalDAV client.

Two signal shapes per event, and they answer different questions. The `start`
key answers "this is about to happen"; the `rev` key answers "this changed."
Both are content-keyed, so neither needs a stored cursor: a start time only
enters the window once, and a revision only appears once per edit.

The tool is asked for everything inside the horizon (default 14 days), not
just the lookahead, so a change to an event still days away is detected as
soon as it happens rather than only once the event becomes imminent (fix
round 1 item B). Only the lookahead decides which events are "starting soon"
here; the horizon only decides which events are watched for changes at all.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from eve.settings import get_settings
from eve.tools_client import invoke
from eve_ambient.types import Signal, tool_result

logger = logging.getLogger(__name__)


def _starts_soon(start: str, lookahead_minutes: int) -> bool:
    try:
        when = datetime.fromisoformat(start)
    except (TypeError, ValueError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when <= datetime.now(UTC) + timedelta(minutes=lookahead_minutes)


async def poll(member_sub: str) -> list[Signal]:
    settings = get_settings()
    lookahead = settings.ambient_calendar_lookahead_minutes
    horizon = settings.ambient_calendar_horizon_days
    result = tool_result(
        await invoke(
            "calendar.list_events",
            {
                "member_sub": member_sub,
                "lookahead_minutes": lookahead,
                "horizon_days": horizon,
            },
        )
    )
    if result is None:
        return []

    signals: list[Signal] = []
    for event in result.get("events") or []:
        if not isinstance(event, dict):
            logger.warning(
                "calendar.list_events returned a non-dict event for %s: %.80s",
                member_sub,
                event,
            )
            continue
        uid = str(event.get("uid") or "")
        if not uid:
            continue
        start = event.get("start")
        if not start:
            # No start means no time to notify about and no dedup key worth
            # trusting - "…, starting None." must never reach the filter
            # (fix round 1 item E).
            logger.warning(
                "calendar event %s for %s has no start, dropping it: %.80s",
                uid,
                member_sub,
                event,
            )
            continue

        title = event.get("summary") or "(untitled event)"
        where = f" at {event['location']}" if event.get("location") else ""
        cancelled = str(event.get("status") or "").upper() == "CANCELLED"
        note = " This event was CANCELLED." if cancelled else ""
        # Both signal kinds are reported as of now: the payload already
        # carries `start` and the summary says it in words, and a future
        # start time under the filter prompt's "Occurred at" label would be
        # a lie (fix round 1 item E).
        occurred = datetime.now(UTC)

        if _starts_soon(start, lookahead):
            signals.append(
                Signal(
                    source="calendar",
                    key=f"{uid}:start:{start}",
                    occurred_at=occurred,
                    member_sub=member_sub,
                    summary=f"Upcoming: {title}{where}, starting {start}.{note}",
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
                    # States only what it knows - that the revision changed -
                    # not that something the reader saw before is now
                    # different. The filter reads only this summary, so
                    # asserting an unestablished change would be the defect
                    # (fix round 1 item C).
                    summary=(
                        f"Calendar entry (rev {revision}): {title}{where}, "
                        f"starting {start}.{note}"
                    ),
                    payload=event,
                )
            )
    return signals
