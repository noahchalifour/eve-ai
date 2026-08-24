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

from eve.family import get_family
from eve.settings import get_settings
from eve_ambient import store
from eve_ambient.pipeline import handle_signal
from eve_ambient.sources import SOURCES, Source
from eve_ambient.sources.home import from_webhook
from eve_ambient.types import Signal

logger = logging.getLogger(__name__)

_background: set[asyncio.Task] = set()

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
            if not await store.has_any(source.name):
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
            logger.info("ambient poll: %s", await poll_once())
            await store.prune_seen()
        except asyncio.CancelledError:
            raise
        except Exception:
            # The loop is the last line of defence. It never dies.
            logger.exception("the ambient poll tick failed outright")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    task = None
    if settings.ambient_enabled:
        task = asyncio.create_task(_poll_forever())
        logger.info("ambient polling every %ss", settings.ambient_poll_interval_seconds)
    else:
        logger.info("ambient is disabled; serving health only")
    yield
    if task:
        task.cancel()
        try:
            await task
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
    return {"status": "ok", "ambient_enabled": get_settings().ambient_enabled}


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
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        payload = await request.json()
        signal = from_webhook(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"unusable payload: {exc}") from exc

    # 202 and a background task: a compose turn takes far longer than Home
    # Assistant will hold the connection open.
    task = asyncio.create_task(_handle_in_background(signal))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return {"accepted": signal.key}


async def _handle_in_background(signal: Signal) -> None:
    try:
        logger.info("webhook signal %s resolved as %s", signal.key, await handle_signal(signal))
    except Exception:
        logger.warning("webhook signal %s failed", signal.key, exc_info=True)
