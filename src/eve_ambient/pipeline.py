"""One signal, from arrival to resolution. The only module that knows the
order things happen in.

The order is a cost order as much as a logic order: the cooldown check is one
indexed SELECT, the filter is a cheap model call, the gates are pure, and the
compose turn is the only expensive step. Nothing expensive runs until
everything cheap has agreed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from eve.family import UnknownMemberError, get_family
from eve.settings import get_settings
from eve_ambient import gates, store
from eve_ambient.filter import judge
from eve_ambient.notify import DeliveryError, deliver
from eve_ambient.ntfy import Notifier, NtfyNotifier
from eve_ambient.types import Signal

logger = logging.getLogger(__name__)


async def handle_signal(
    signal: Signal, *, now: datetime | None = None, notifier: Notifier | None = None
) -> str:
    settings = get_settings()
    now = now or datetime.now(UTC)
    notifier = notifier or NtfyNotifier()

    cooldown = (
        signal.cooldown_hours
        if signal.cooldown_hours is not None
        else settings.ambient_cooldown_hours
    )
    if not await store.is_fresh(signal.source, signal.key, cooldown):
        return "stale"

    verdict = await judge(signal)
    audience = gates.permitted(signal, gates.scoped_audience(signal, verdict.audience))
    if not verdict.notify or not verdict.audience:
        await store.mark_seen(signal.source, signal.key)
        return _resolved(signal, verdict, audience, "filtered")
    if not audience:
        await store.mark_seen(signal.source, signal.key)
        return _resolved(signal, verdict, audience, "unpermitted")

    family = get_family()
    outcomes: list[str] = []
    deferred = False

    for sub in audience:
        try:
            member = family.get(sub)
        except UnknownMemberError:
            continue

        if not verdict.urgent:
            local = gates.local_now(member.timezone, now)
            if gates.in_quiet_hours(local, settings.ambient_quiet_hours):
                logger.info("holding %s for %s: quiet hours", signal.key, sub)
                outcomes.append("quiet")
                continue
            sent_today = await store.notices_since(
                sub, gates.day_start_utc(member.timezone, now)
            )
            if sent_today >= settings.ambient_daily_cap:
                logger.info("holding %s for %s: daily cap", signal.key, sub)
                outcomes.append("capped")
                continue
        else:
            logger.warning(
                "URGENT bypass of cap and quiet hours: source=%s key=%s member=%s why=%s",
                signal.source, signal.key, sub, verdict.why,
            )

        try:
            thread_id = await deliver(signal, member, verdict, notifier)
        except DeliveryError:
            logger.warning("deferring %s for %s", signal.key, sub, exc_info=True)
            deferred = True
            continue
        if thread_id is None:
            outcomes.append("vetoed")
            continue
        await store.record_notice(
            sub, signal.source, signal.key, verdict.urgent, thread_id
        )
        outcomes.append("sent")

    if deferred:
        # Left unseen deliberately: the next poll retries it (design 6.4).
        return _resolved(signal, verdict, audience, "deferred")

    await store.mark_seen(signal.source, signal.key)
    for candidate in ("sent", "vetoed", "capped", "quiet"):
        if candidate in outcomes:
            return _resolved(signal, verdict, audience, candidate)
    return _resolved(signal, verdict, audience, "filtered")


def _resolved(signal, verdict, audience, outcome: str) -> str:
    """One line per signal, whatever happened to it (design section 9). The
    Langfuse trace only starts at the compose turn, so everything before it —
    the verdict, the reasoning, who survived the permission gate, and which
    gate stopped it — exists here or nowhere. It is the difference between
    "Eve is too noisy" being diagnosable and being an argument.
    """
    logger.info(
        "ambient resolved source=%s key=%s outcome=%s notify=%s urgent=%s "
        "audience=%s why=%s",
        signal.source, signal.key, outcome, verdict.notify, verdict.urgent,
        ",".join(audience) or "none", verdict.why,
    )
    return outcome
