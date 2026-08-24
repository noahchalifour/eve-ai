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

from fastapi import FastAPI, Header, HTTPException, Request

from eve.family import get_family
from eve.settings import get_settings
from eve_ambient import store
from eve_ambient.pipeline import handle_signal
from eve_ambient.sources import SOURCES
from eve_ambient.sources.home import from_webhook

logger = logging.getLogger(__name__)

_background: set[asyncio.Task] = set()


def _audience_for(source) -> list[str]:
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
            signals = []
            for sub in _audience_for(source):
                signals.extend(await source.poll(sub))
        except Exception:
            logger.warning("source %s failed this tick", source.name, exc_info=True)
            counts["errors"] += 1
            continue

        first_poll = not await store.has_any(source.name)
        if first_poll:
            for signal in signals:
                await store.mark_seen(signal.source, signal.key)
                counts["primed"] += 1
            logger.info(
                "primed %s with %d existing signals; notifying on none of them",
                source.name, counts["primed"],
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
    if not secret or x_eve_ambient_secret != secret:
        raise HTTPException(status_code=401, detail="unauthorized")
    payload = await request.json()
    try:
        signal = from_webhook(payload)
    except (KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"unusable payload: {exc}") from exc

    # 202 and a background task: a compose turn takes far longer than Home
    # Assistant will hold the connection open.
    task = asyncio.create_task(_handle_in_background(signal))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return {"accepted": signal.key}


async def _handle_in_background(signal) -> None:
    try:
        logger.info("webhook signal %s resolved as %s", signal.key, await handle_signal(signal))
    except Exception:
        logger.warning("webhook signal %s failed", signal.key, exc_info=True)
