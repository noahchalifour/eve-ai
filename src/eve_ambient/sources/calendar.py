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
from eve_ambient.types import Signal, SourceUnavailable, list_field, tool_result

logger = logging.getLogger(__name__)


def _starts_soon(start: str, lookahead_minutes: int) -> bool:
    try:
        when = datetime.fromisoformat(start)
    except (TypeError, ValueError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    # Bounded below by now (fix round 4, item 6): without a lower bound this
    # answered true for any event already in the past, so an all-day event -
    # which renders as midnight UTC - announced itself as "Upcoming" at
    # whatever hour the poll first saw it, and kept doing so on every later
    # tick the search's overlapping-window match still returned it in.
    return now <= when <= now + timedelta(minutes=lookahead_minutes)


def _starts_later(start: str, lookahead_minutes: int) -> bool:
    """True only when the event's start is beyond the lookahead - not yet
    imminent, and not already past either (rereview fix, item 1).

    Before this, the bare `:rev:` branch fired whenever `_starts_soon` was
    false, which included an event that had already begun: `_starts_soon`'s
    lower bound (item 6) stops the *start* key from re-firing once an event
    starts, but nothing stopped the *bare rev* key from firing instead, as a
    brand-new signal, the moment the event crossed that same boundary - the
    CalDAV search keeps returning an event that still overlaps the horizon
    window after it starts, and `_to_dict` always sets a revision. That was
    a second filter call, compose turn, thread and push per event, displaced
    by however long the poll took to notice the event had started, and its
    summary announced an event already in progress as something newly
    changed.
    """
    try:
        when = datetime.fromisoformat(start)
    except (TypeError, ValueError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when > datetime.now(UTC) + timedelta(minutes=lookahead_minutes)


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
        # Not "no events" - eve-tools' call failed or returned garbage, and
        # priming must be able to tell the two apart (fix round 4, item 2).
        # `poll_once` already isolates and counts a raising member.
        raise SourceUnavailable("calendar.list_events did not return usable JSON")

    signals: list[Signal] = []
    for event in list_field(result, "events"):
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

        revision = event.get("revision")
        # A revision that changes while the event is already imminent used
        # to fire both the `:start:` and the `:rev:` key for the same
        # event - a dentist appointment created 30 minutes before it starts
        # produced two filter calls, two compose turns, two threads and two
        # pushes (fix round 4, item 7). Folding the revision into the start
        # key rather than suppressing `rev` outright inside the lookahead
        # keeps a cancellation of an imminent event notifying: the key still
        # changes when the revision does, and the CANCELLED note is already
        # part of this same summary. The bare `:rev:` signal is reserved for
        # events not yet imminent, where it is the only way a change reaches
        # Eve before the event's own start window does.
        if _starts_soon(start, lookahead):
            key = f"{uid}:start:{start}:{revision}" if revision else f"{uid}:start:{start}"
            signals.append(
                Signal(
                    source="calendar",
                    key=key,
                    occurred_at=occurred,
                    member_sub=member_sub,
                    summary=f"Upcoming: {title}{where}, starting {start}.{note}",
                    payload=event,
                )
            )
        elif revision and _starts_later(start, lookahead):
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
