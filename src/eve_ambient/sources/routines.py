"""Due routines as signals.

`per_member=False`, like `finances`, `computer` and `coding`: the query is
household-wide by construction and every claimed row carries its own member,
so polling once per member would issue one query per member to answer the
same question.

The relevance filter is bypassed for this source (see
`eve_ambient.pipeline._REQUESTED_SOURCES`), and so are quiet hours and the
daily cap. A routine is the most direct request in the system: the member did
not merely ask once, they asked for it to keep happening. An LLM deciding
that the answer to a standing request is "not relevant" and swallowing it is
the worst failure mode available, and a shared daily counter would let a
chatty calendar starve a routine the member deliberately created. The member
controls this spend by editing the routine, which they can see.

Quiet hours are bypassed for the same reason, with the schedule as the
mitigation: cadence is authored in the member's own timezone, so a member who
does not want a 3am notification schedules the routine for 8am.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from eve.routines import cadence as cadence_rules, store
from eve.settings import get_settings
from eve_ambient.types import Signal

logger = logging.getLogger(__name__)

SOURCE_NAME = "routines"

# A routine's own cadence is the only thing that should decide when it runs
# again, so the pipeline's cooldown must never suppress a legitimate firing.
# The key already carries the scheduled instant, so two firings are never the
# same key; this bound exists only to make a retry of one occurrence idempotent.
COOLDOWN_HOURS = 1

# What a pipeline resolution means for the routine's own record. `stale`,
# `filtered` and anything else deliberately map to nothing: no run happened.
_OUTCOMES = {
    "sent": "spoke",
    "vetoed": "silent",
    "deferred": "error",
    "error": "error",
}


def _summary(routine: dict) -> str:
    return f"Routine due: {routine['title']} ({cadence_rules.describe(routine['cadence'])})."


async def poll(_member_sub: str) -> list[Signal]:
    """Claim every due routine and turn each into one signal.

    `store.claim_due` has already advanced `next_run_at` by the time this
    sees a row, which is what stops a compose turn still running on the next
    tick from firing the same occurrence twice. This function then computes
    and writes the real next time from the cadence.

    A routine past its expiry is retired here rather than fired: the tick
    that would have run it is the natural place to notice, and it costs no
    extra query.
    """
    now = datetime.now(UTC)
    try:
        claimed = await store.claim_due(now)
    except Exception:
        logger.warning("could not claim due routines this tick", exc_info=True)
        return []

    signals = []
    for routine in claimed:
        try:
            expires_at = routine.get("expires_at")
            if expires_at is not None and expires_at <= now:
                await store.expire(routine["id"])
                continue

            # Scheduled before the signal is emitted, and from `now` rather
            # than from the missed slot, so a service that was down overnight
            # fires once and moves on instead of delivering a backlog.
            await store.schedule_next(
                routine["id"],
                cadence_rules.next_after(routine["cadence"], routine["timezone"], now),
            )

            scheduled_for = routine["scheduled_for"]
            signals.append(
                Signal(
                    source=SOURCE_NAME,
                    key=f"{routine['id']}:{scheduled_for.isoformat()}",
                    occurred_at=scheduled_for,
                    member_sub=routine["member_sub"],
                    summary=_summary(routine),
                    payload={
                        "routine_id": routine["id"],
                        "title": routine["title"],
                        "instruction": routine["instruction"],
                        "cadence": routine["cadence"],
                        "created_at": routine["created_at"].isoformat(),
                        "last_run_at": (
                            routine["last_run_at"].isoformat()
                            if routine.get("last_run_at")
                            else None
                        ),
                    },
                    cooldown_hours=COOLDOWN_HOURS,
                )
            )
        except Exception:
            # One malformed routine must not cost every other member their
            # tick. The row keeps its advanced next_run_at, so a persistently
            # broken cadence is skipped rather than retried in a tight loop.
            logger.warning(
                "could not turn routine %s into a signal", routine.get("id"),
                exc_info=True,
            )
            continue
    return signals


async def record_outcome(signal: Signal, resolution: str) -> None:
    """Write this firing's result back to the routine.

    Called by `eve_ambient.pipeline.handle_signal` once the resolution is
    known. Only infrastructure failure counts against the auto-pause: a
    `vetoed` resolution means Eve looked and found nothing worth saying,
    which is the routine working.
    """
    if signal.source != SOURCE_NAME:
        return
    outcome = _OUTCOMES.get(resolution)
    if outcome is None:
        return
    routine_id = signal.payload.get("routine_id")
    if not routine_id:
        return
    try:
        await store.record_run(
            routine_id, outcome, get_settings().routine_failure_limit
        )
    except Exception:
        # The notification has already been delivered. Losing this row is
        # strictly better than letting it escape and re-resolve the signal.
        logger.warning(
            "could not record the run for routine %s", routine_id, exc_info=True
        )
