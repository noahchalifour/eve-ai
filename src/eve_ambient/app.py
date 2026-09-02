"""The eve-ambient service: a webhook, a poll loop, and a health endpoint.

One replica only. Nothing here elects a leader, and two instances would
double-count the daily cap.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hmac import compare_digest

from fastapi import FastAPI, Header, HTTPException, Request

from eve.coding import supervisor
from eve.family import get_family
from eve.settings import get_settings
from eve_ambient import store
from eve_ambient.pipeline import handle_signal
from eve_ambient.sources import SOURCES, Source
from eve_ambient.sources.home import from_webhook
from eve_ambient.types import Signal, SourcePollError

logger = logging.getLogger(__name__)

_background: set[asyncio.Task] = set()

# Which `(source, key)` webhook signals are currently being handled, so a
# second concurrent post for the same key can be deduped before it ever
# reaches the gate chain (fix round 4, item 4): by design no `eve_ambient_seen`
# row exists until a signal resolves, so two concurrent posts for the same
# key would otherwise both pass `is_fresh` and `already_notified` and both
# deliver. Home Assistant automations commonly fire duplicate triggers, so
# this needs no failure at all to happen.
_in_flight: set[tuple[str, str]] = set()

# Bounds total concurrent webhook-triggered compose turns (fix round 4, item
# 4): one leaked secret would otherwise buy unbounded concurrent REFLEX and
# VOICE spend, and the daily cap sits after the filter, so it does not bound
# this on its own.
_MAX_CONCURRENT_WEBHOOK_SIGNALS = 5
_webhook_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_WEBHOOK_SIGNALS)

# Marked once per source, on the tick that primes it, whether or not that
# tick found anything to prime. Priming has to be an explicit fact rather
# than "this source has at least one seen row": an empty first poll (an
# empty inbox, no transactions yet, nothing in the calendar window) would
# otherwise leave no row behind at all, so `has_any` would still read false
# on the next tick - the first one to actually produce a signal - and that
# tick would be silently primed away instead of notified.
_PRIMED_SENTINEL = "__primed__"

# How long shutdown waits for in-flight webhook deliveries (compose turns
# already running when the process stops) before giving up on them.
_BACKGROUND_DRAIN_TIMEOUT_SECONDS = 10.0

# The last completed tick's timestamp and outcome counts, exposed on
# `/healthz` (fix round 4, item 10): before this, `/healthz` carried no
# functional signal at all, so the Gatus check the design specifies stayed
# green through a week of per-tick errors. `last_tick_at` only advances on a
# tick that actually returned - a `poll_once` that raises outright leaves it
# stale, which is what lets an alert assert on staleness.
_last_tick: dict = {"at": None, "counts": {}}


def _audience_for(source: Source) -> list[str]:
    """Which subs to poll this source for. A per-member source is polled only
    for members holding its permission, so an ungranted member costs no API
    call rather than being filtered after the fact."""
    if not source.per_member:
        return [""]
    return [m.sub for m in get_family().members() if m.can(source.permission)]


async def poll_once(now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    counts: Counter[str] = Counter()
    for source in SOURCES:
        try:
            signals: list[Signal] = []
            member_failed = False
            for sub in _audience_for(source):
                try:
                    signals.extend(await source.poll(sub))
                except SourcePollError as exc:
                    # Part of this source's work succeeded and part failed
                    # (finances.py's two independent eve-tools calls) - the
                    # successful half's signals ride along on the exception
                    # rather than being discarded for as long as the failing
                    # half's outage lasts. Still counts as a failure for
                    # priming purposes below: `signals` gains `exc.partial`,
                    # but the priming branch ignores `signals` entirely on a
                    # `member_failed` tick, the same as it always has.
                    logger.warning(
                        "source %s poll failed for member %r; keeping %d partial "
                        "signal(s)",
                        source.name, sub, len(exc.partial),
                        exc_info=True,
                    )
                    counts["errors"] += 1
                    member_failed = True
                    signals.extend(exc.partial)
                except Exception:
                    # One member's failure (an expired token, a rate limit)
                    # must not discard the signals already collected for
                    # everyone else polled under this same source.
                    logger.warning(
                        "source %s poll failed for member %r", source.name, sub,
                        exc_info=True,
                    )
                    counts["errors"] += 1
                    member_failed = True

            # `has_any` and the priming `mark_seen` live inside this same
            # try/except: a transient database error here must not escape
            # `poll_once` and skip every source after this one for the tick.
            if source.name != "computer" and not await store.has_any(source.name):
                if member_failed:
                    # Priming only happens once every member has actually
                    # been polled successfully. `signals` here can't be
                    # told apart from "nothing to prime" - priming on a
                    # partial (or total) failure would mark seen a backlog
                    # nobody has actually seen, so the eventual real
                    # backlog would surface all at once as live
                    # notifications the moment the credential is fixed:
                    # precisely what priming exists to prevent. Leave the
                    # source unprimed; the next tick is soon and tries
                    # again.
                    logger.info(
                        "not priming %s this tick: at least one member's poll failed",
                        source.name,
                    )
                    continue
                primed = 0
                for signal in signals:
                    await store.mark_seen(signal.source, signal.key)
                    primed += 1
                await store.mark_seen(source.name, _PRIMED_SENTINEL)
                counts["primed"] += primed
                # WARNING, not INFO, specifically when primed against
                # nothing (fix round 4, item 2, unattended-operation note):
                # this is the one line recording that a source's next real
                # signal will be judged against an assumed-empty backlog,
                # and it needs to still be there on Friday.
                logger.log(
                    logging.WARNING if primed == 0 else logging.INFO,
                    "primed %s with %d existing signals; notifying on none of them",
                    source.name, primed,
                )
                continue

            for signal in signals:
                try:
                    counts[await handle_signal(signal, now=now)] += 1
                except Exception:
                    logger.warning(
                        "signal %s/%s failed", signal.source, signal.key, exc_info=True
                    )
                    counts["errors"] += 1
        except Exception:
            logger.warning("source %s failed this tick", source.name, exc_info=True)
            counts["errors"] += 1
            continue
    return dict(counts)


async def _poll_forever() -> None:
    interval = get_settings().ambient_poll_interval_seconds
    while True:
        try:
            counts = await poll_once()
            _last_tick["at"] = datetime.now(UTC).isoformat()
            _last_tick["counts"] = counts
            logger.info("ambient poll: %s", counts)
            await store.prune_seen()
        except asyncio.CancelledError:
            raise
        except Exception:
            # The loop is the last line of defence. It never dies.
            logger.exception("the ambient poll tick failed outright")
        await asyncio.sleep(interval)


async def _supervise_forever() -> None:
    """The coding supervisor's own tick, deliberately not the ambient one.

    This is a control loop with an agent waiting on the other end, not a
    notification pipeline: 300s of latency per conversational turn would
    make Eve a worse correspondent than the member who delegated the work.

    It only drives conversations forward. Resolved sessions are turned into
    signals by `sources.coding.poll` on the ambient tick, which is where the
    permission gate, the quiet hours, and the daily cap live - none of which
    a control loop has any business bypassing.
    """
    interval = get_settings().coding_supervisor_interval_seconds
    while True:
        try:
            await supervisor.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Same posture as _poll_forever: the loop is the last line of
            # defence and never dies.
            logger.exception("the coding supervisor tick failed outright")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    task = None
    supervisor_task = None
    if settings.ambient_enabled:
        task = asyncio.create_task(_poll_forever())
        logger.info("ambient polling every %ss", settings.ambient_poll_interval_seconds)
    else:
        logger.info("ambient is disabled; serving health only")
    if settings.coding_enabled:
        supervisor_task = asyncio.create_task(_supervise_forever())
        logger.info(
            "coding supervisor ticking every %ss",
            settings.coding_supervisor_interval_seconds,
        )
    yield
    for running_task in (task, supervisor_task):
        if running_task:
            running_task.cancel()
            try:
                await running_task
            except asyncio.CancelledError:
                pass
    if _background:
        # A compose turn in flight at shutdown gets a chance to finish -
        # possibly after the push but before the notice row is written -
        # rather than being destroyed mid-way.
        _done, pending = await asyncio.wait(
            _background, timeout=_BACKGROUND_DRAIN_TIMEOUT_SECONDS
        )
        if pending:
            logger.warning(
                "%d webhook signal(s) still in flight at shutdown", len(pending)
            )


app = FastAPI(title="eve-ambient", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "status": "ok",
        "ambient_enabled": get_settings().ambient_enabled,
        "last_tick_at": _last_tick["at"],
        "last_tick_counts": _last_tick["counts"],
    }


@app.post("/signals/home-assistant", status_code=202)
async def home_assistant_signal(
    request: Request,
    x_eve_ambient_secret: str | None = Header(default=None),
) -> dict:
    secret = get_settings().ambient_ha_webhook_secret
    presented = x_eve_ambient_secret or ""
    # `compare_digest` on the `.encode()`d bytes, not the `str` operands
    # themselves - same reasoning as `eve.auth._ambient_subject`:
    # `compare_digest` raises `TypeError` on a `str` operand containing
    # non-ASCII, which a hostile or merely malformed header can trigger.
    if not secret or not compare_digest(presented.encode(), secret.encode()):
        # (fix round 4, item 11) Without this, an operator cannot tell a
        # wrong secret from an automation that never fired - both look like
        # silence. The presented secret is deliberately not logged.
        logger.warning("rejected home-assistant webhook: invalid or missing secret")
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        payload = await request.json()
        signal = from_webhook(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"unusable payload: {exc}") from exc

    if not get_settings().ambient_enabled:
        # The one lever an operator has to silence Eve at 3am (fix round 4,
        # item 1): `lifespan` was the only consumer of `ambient_enabled`, so
        # a deployment with the webhook secret set but ambient disabled
        # still ran the filter, spent a VOICE-tier turn, created a thread and
        # pushed. Checked here, after the secret and the payload shape are
        # already known good, and before any of that expensive work starts.
        # 503, not 404: the endpoint exists, the service behind it is
        # switched off.
        raise HTTPException(status_code=503, detail="ambient is disabled")

    dedup_key = (signal.source, signal.key)
    if dedup_key in _in_flight:
        logger.info(
            "webhook signal %s is already in flight; not queuing a duplicate",
            signal.key,
        )
        return {"accepted": signal.key}
    _in_flight.add(dedup_key)

    # 202 and a background task: a compose turn takes far longer than Home
    # Assistant will hold the connection open.
    task = asyncio.create_task(_handle_in_background(signal))
    _background.add(task)
    task.add_done_callback(_background.discard)
    task.add_done_callback(lambda _task, key=dedup_key: _in_flight.discard(key))
    return {"accepted": signal.key}


async def _handle_in_background(signal: Signal) -> None:
    try:
        async with _webhook_semaphore:
            logger.info(
                "webhook signal %s resolved as %s", signal.key, await handle_signal(signal)
            )
    except Exception:
        logger.warning("webhook signal %s failed", signal.key, exc_info=True)
