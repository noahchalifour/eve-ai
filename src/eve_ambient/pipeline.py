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
from eve_ambient.filter import FilterError, judge
from eve_ambient.notify import DeliveryError, deliver
from eve_ambient.ntfy import Notifier, NtfyNotifier
from eve_ambient.types import FilterVerdict, Signal

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
        return _resolved(signal, None, [], "stale")

    try:
        verdict = await judge(signal)
    except FilterError:
        # A couldn't-decide, not a decided-no (fix round 1, item 2): treat it
        # exactly like a notify.DeliveryError and leave the signal unseen so
        # the next poll retries it. A persistent outage retrying every poll
        # is correct and cheap — the filter call fails fast.
        logger.warning(
            "deferring %s: the filter could not judge it", signal.key, exc_info=True
        )
        return _resolved(signal, None, [], "deferred")

    # The roster is only worth reading once we know somebody might be told
    # something (fix round 1, item 5): `gates.permitted` calls `get_family`,
    # and a non-event should never pay for it, cached or not.
    if not verdict.notify or not verdict.audience:
        await store.mark_seen(signal.source, signal.key)
        return _resolved(signal, verdict, [], "filtered")

    audience = gates.permitted(signal, gates.scoped_audience(signal, verdict.audience))
    if not audience:
        await store.mark_seen(signal.source, signal.key)
        return _resolved(signal, verdict, [], "unpermitted")

    family = get_family()
    outcomes: list[str] = []
    deferred = False
    already_known = False

    for sub in audience:
        try:
            member = family.get(sub)
        except UnknownMemberError:
            continue

        if await store.already_notified(sub, signal.source, signal.key, cooldown):
            # A previous pass already delivered to this member — the usual
            # cause is the survivor of an earlier partial defer (fix round 1,
            # item 1). Retrying must not re-run a paid compose turn, re-push,
            # or re-spend the daily cap for someone who already has it.
            # Bounded by the same cooldown window as the freshness check
            # (fix round 2, item 1), so a recurrence past that window is not
            # this: it is a fresh delivery.
            logger.info("skipping %s for %s: already notified", sub, signal.key)
            already_known = True
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
        try:
            await store.record_notice(
                sub, signal.source, signal.key, verdict.urgent, thread_id
            )
        except Exception:
            # The push already happened and the thread already exists (fix
            # round 4, item 5): a database failure on this one statement
            # after a successful `deliver` must not escape and leave the
            # signal unseen, or the next poll finds no notice row and
            # re-delivers - a second 3am push for a lost cap-counter row.
            # Losing that row is strictly better than duplicating the push.
            logger.error(
                "could not record the notice for %s/%s; the message was already "
                "sent and will not be resent",
                sub, signal.key, exc_info=True,
            )
        outcomes.append("sent")

    if deferred:
        # Left unseen deliberately: the next poll retries it (design 6.4).
        # store.already_notified above is what makes that retry idempotent
        # per member rather than a duplicate notification to whoever already
        # got it on this pass.
        return _resolved(signal, verdict, audience, "deferred")

    await store.mark_seen(signal.source, signal.key)
    for candidate in ("sent", "vetoed", "capped", "quiet"):
        if candidate in outcomes:
            return _resolved(signal, verdict, audience, candidate)
    if already_known:
        # Every member in the audience already had a notice for this signal
        # within its cooldown window (fix round 2, item 3): distinct from
        # "filtered", which means the filter itself said no. Collapsing the
        # two would make the one designed trace line ambiguous between "the
        # filter said no" and "everyone already knew."
        return _resolved(signal, verdict, audience, "known")
    return _resolved(signal, verdict, audience, "filtered")


def _resolved(
    signal: Signal, verdict: FilterVerdict | None, audience: list[str], outcome: str
) -> str:
    """One line per signal, whatever happened to it (design section 9). The
    Langfuse trace only starts at the compose turn, so everything before it —
    the verdict, the reasoning, who survived the permission gate, and which
    gate stopped it — exists here or nowhere. It is the difference between
    "Eve is too noisy" being diagnosable and being an argument.

    `verdict` is `None` for the two paths that resolve before a verdict
    exists at all — `stale` (the filter never ran) and `deferred` by a
    `FilterError` (the filter ran but could not answer) — so this still
    emits a line for the cooldown path, which is both the most frequently
    taken one and, without this, the one that left no trace (fix round 1,
    item 4).
    """
    notify = verdict.notify if verdict is not None else False
    urgent = verdict.urgent if verdict is not None else False
    why = verdict.why if verdict is not None else "n/a"
    logger.info(
        "ambient resolved source=%s key=%s outcome=%s notify=%s urgent=%s "
        "audience=%s why=%s",
        signal.source, signal.key, outcome, notify, urgent,
        ",".join(audience) or "none", why,
    )
    return outcome
