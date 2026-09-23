"""The eve-ambient service: a webhook, a poll loop, and a health endpoint.

One replica only. Nothing here elects a leader, and two instances would
double-count the daily cap.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from hmac import compare_digest

from fastapi import FastAPI, Header, HTTPException, Request

from eve.coding import supervisor
from eve.family import UnknownMemberError, get_family
from eve.settings import get_settings
from eve_ambient import store
from eve_ambient.debounce import Debouncer
from eve_ambient.pipeline import handle_signal
from eve_ambient.sources import SOURCES, Source
from eve_ambient.sources import github
from eve_ambient.sources.home import from_webhook
from eve_ambient.types import Signal, SourcePollError
from eve_linear import activities
from eve_linear import handler as linear_handler
from eve_linear.verify import timestamp_is_fresh, verify_signature

logger = logging.getLogger(__name__)

_background: set[asyncio.Task] = set()
# EVE-32 and EVE-31: pushes and comments arrive in bursts, and each would
# otherwise start a paid session. Keyed per pull request, so a burst on one
# does not delay another.
_debounce = Debouncer()

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

# Linear-originated work gets its own in-flight set and semaphore rather than
# sharing the ambient ones: a burst of delegations must not starve the Home
# Assistant path, and the two have different natural concurrencies.
_linear_in_flight: set[str] = set()

# Unlike `_webhook_semaphore`, this bound is configurable
# (`linear_max_live_sessions`), so it cannot be a module-level literal built
# at import time: `Settings()` reads env at construction, and module import
# in tests happens before env vars are set. `_get_linear_semaphore` builds it
# lazily on first use instead, and only rebuilds it if the configured bound
# itself has changed since (which happens across a settings cache-clear in
# tests; a live deployment reads its env once and the bound does not move
# mid-process).
_linear_semaphore: asyncio.Semaphore | None = None
_linear_semaphore_bound: int | None = None


def _get_linear_semaphore() -> asyncio.Semaphore:
    global _linear_semaphore, _linear_semaphore_bound
    bound = get_settings().linear_max_live_sessions
    if _linear_semaphore is None or _linear_semaphore_bound != bound:
        _linear_semaphore = asyncio.Semaphore(bound)
        _linear_semaphore_bound = bound
    return _linear_semaphore

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
            # `computer` and `routines` are both exempt from priming: each
            # one's signal is always a direct response to something a member
            # explicitly asked for (they dispatched the task, or they
            # created the routine), so silently priming it away the first
            # time would drop something they're waiting on. `routines` is
            # additionally never a backlog-flood risk in the first place -
            # a member has at most a handful of standing routines, each on
            # an hours-to-weekly cadence - so exempting it costs nothing,
            # unlike a chatty per-member source with years of history.
            if source.name not in ("computer", "routines") and not await store.has_any(
                source.name
            ):
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
    await _debounce.cancel_all()
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


@app.post("/signals/github", status_code=202)
async def github_signal(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict:
    """GitHub's pull-request webhook.

    The signature is verified against the RAW body before any parsing: a
    body that reparses differently than it hashed is the classic bypass.

    Unlike the other webhook routes, this one does not go through
    `handle_signal`/`notify.deliver`: `eve.review.dispatch.start` is not a
    tool bound to Eve's chat graph, so routing this through the ambient
    notification pipeline would run an LLM turn that has no way to actually
    start a review. This calls `dispatch.start` directly instead, in a
    background task, using the existing `_in_flight`/`_background` sets only
    for dedup and shutdown-draining.
    """
    settings = get_settings()
    body = await request.body()
    if not github.verify(settings.review_webhook_secret, x_hub_signature_256 or "", body):
        logger.warning("rejected github webhook: invalid or missing signature")
        raise HTTPException(status_code=401, detail="unauthorized")

    if x_github_event in github.FEEDBACK:
        return _feedback_event(x_github_event, body)

    if x_github_event != "pull_request":
        # Including `ping`, which GitHub sends on hook creation. Rejecting it
        # makes a correctly configured hook look broken in the GitHub UI.
        return {"accepted": None}

    try:
        payload = json.loads(body)
    except ValueError:
        return {"accepted": None}
    if payload.get("action") == "synchronize":
        return _synchronize_event(payload)
    try:
        payload_parsed = github.from_webhook(payload)
    except ValueError:
        # An action that does not commission a review is not an error: most
        # pull-request events are `closed` and `edited`.
        return {"accepted": None}

    if not settings.review_enabled:
        raise HTTPException(status_code=503, detail="reviewing is disabled")

    try:
        member = get_family().by_github_login(payload_parsed.payload["actor"])
    except UnknownMemberError:
        logger.warning(
            "refusing a review for unknown GitHub login %r", payload_parsed.payload["actor"]
        )
        raise HTTPException(status_code=403, detail="unknown actor") from None

    if not member.can("code.review"):
        logger.warning(
            "refusing a review for %s: missing code.review permission",
            payload_parsed.payload["actor"],
        )
        raise HTTPException(status_code=403, detail="missing code.review permission") from None

    dedup_key = ("review", payload_parsed.key)
    if dedup_key in _in_flight:
        return {"accepted": payload_parsed.key}
    _in_flight.add(dedup_key)

    async def _start_review() -> None:
        from eve.review import dispatch
        try:
            result = await dispatch.start(
                repo=payload_parsed.payload["repo"],
                pr_number=payload_parsed.payload["pr_number"],
                head_sha=payload_parsed.payload["head_sha"],
                base_ref=payload_parsed.payload["base_ref"],
                member_sub=member.sub,
            )
            logger.info("review dispatch for %s: %s", payload_parsed.key, result)
        except Exception:
            logger.warning("review dispatch for %s failed", payload_parsed.key, exc_info=True)

    task = asyncio.create_task(_start_review())
    _background.add(task)
    task.add_done_callback(_background.discard)
    task.add_done_callback(lambda _task, key=dedup_key: _in_flight.discard(key))
    return {"accepted": payload_parsed.key}


def _synchronize_event(payload: dict) -> dict:
    """New commits on a pull request (EVE-32). Schedules a re-review for
    when the branch has gone quiet; `dispatch.restart_on_push` decides then
    whether one is owed. No actor check here, deliberately: the pusher is
    not the one asking. The member who asked for the original review is,
    and their grant is re-checked when the debounce fires.

    Acknowledged rather than refused when disabled: a push is not a request,
    and a 503 would make a hook that also carries labels look broken.
    """
    settings = get_settings()
    if not (settings.review_enabled and settings.review_on_push):
        return {"accepted": None}
    try:
        pushed = github.from_synchronize(payload)
    except ValueError:
        return {"accepted": None}
    if pushed["repo"] not in settings.review_repos:
        return {"accepted": None}

    async def _rereview() -> None:
        from eve.review import dispatch
        result = await dispatch.restart_on_push(**pushed)
        logger.info(
            "re-review for %s#%s@%s: %s",
            pushed["repo"], pushed["pr_number"], pushed["head_sha"][:8], result,
        )

    # Each push replaces the pending action, so what finally runs names the
    # newest head.
    _debounce.schedule(
        ("review", pushed["repo"], pushed["pr_number"]),
        settings.review_debounce_seconds,
        _rereview,
    )
    return {"accepted": f"{pushed['repo']}#{pushed['pr_number']}@{pushed['head_sha']}"}


def _feedback_event(event: str, body: bytes) -> dict:
    """A review or comment on a pull request (EVE-31). Schedules a follow-up
    for when the thread has gone quiet; `followup.start` decides then whether
    the pull request is one Eve opened.

    Only feedback from a family member counts, and never Eve's own: her
    replies, and the reviews EVE-27 posts under her identity, must not wake
    her up to answer herself.
    """
    settings = get_settings()
    if not settings.pr_followup_enabled:
        return {"accepted": None}
    try:
        feedback = github.feedback_from_webhook(event, json.loads(body))
    except ValueError:
        return {"accepted": None}

    from eve.coding import followup
    if not followup.is_trusted(feedback["author"]):
        logger.info(
            "ignoring %s on %s#%s from %r: not someone Eve works for",
            event, feedback["repo"], feedback["pr_number"], feedback["author"],
        )
        return {"accepted": None}

    async def _address() -> None:
        result = await followup.start(
            feedback["repo"], feedback["pr_number"], feedback["pr_url"]
        )
        logger.info(
            "follow-up for %s#%s: %s", feedback["repo"], feedback["pr_number"], result
        )

    _debounce.schedule(
        ("followup", feedback["repo"], feedback["pr_number"]),
        settings.pr_followup_debounce_seconds,
        _address,
    )
    return {"accepted": f"{feedback['repo']}#{feedback['pr_number']}"}


async def _handle_in_background(signal: Signal) -> None:
    try:
        async with _webhook_semaphore:
            logger.info(
                "webhook signal %s resolved as %s", signal.key, await handle_signal(signal)
            )
    except Exception:
        logger.warning("webhook signal %s failed", signal.key, exc_info=True)


@app.post("/signals/linear", status_code=202)
async def linear_signal(request: Request) -> dict:
    """Linear wants a response within 5 seconds and a first activity within
    10. Everything on this path is local computation; the dispatch, which is
    neither, runs in a background task.

    The raw body is read before parsing, because the signature covers the
    exact bytes Linear sent and re-serializing parsed JSON changes them.
    """
    raw = await request.body()
    settings = get_settings()
    if not verify_signature(
        raw, request.headers.get("linear-signature"), settings.linear_webhook_secret
    ):
        # The presented signature is deliberately not logged.
        logger.warning("rejected linear webhook: invalid or missing signature")
        raise HTTPException(status_code=401, detail="unauthorized")

    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"unusable payload: {exc}") from exc

    if not timestamp_is_fresh(payload.get("webhookTimestamp")):
        # A replayed capture, or a clock we cannot reason about. 401 rather
        # than 422: this is an authentication failure, not a shape problem.
        logger.warning("rejected linear webhook: stale or missing timestamp")
        raise HTTPException(status_code=401, detail="unauthorized")

    event = linear_handler.parse_event(payload)
    if not event.session_id:
        raise HTTPException(status_code=422, detail="no agent session in payload")

    if not settings.linear_enabled:
        # Same lever, same position, same reason as the ambient check above.
        raise HTTPException(status_code=503, detail="linear is disabled")

    if event.action not in ("created", "prompted"):
        # Acknowledged and ignored. A 4xx would make Linear retry an event
        # this feature will never handle.
        logger.info("ignoring linear action %r", event.action)
        return {"accepted": event.session_id}

    if event.session_id in _linear_in_flight:
        logger.info(
            "linear session %s is already in flight; not queuing a duplicate",
            event.session_id,
        )
        return {"accepted": event.session_id}
    _linear_in_flight.add(event.session_id)

    task = asyncio.create_task(_handle_linear_in_background(event))
    _background.add(task)
    task.add_done_callback(_background.discard)
    task.add_done_callback(
        lambda _task, key=event.session_id: _linear_in_flight.discard(key)
    )
    return {"accepted": event.session_id}


async def _handle_linear_in_background(event) -> None:
    try:
        semaphore = _get_linear_semaphore()
        # `locked()` and the fall-through into `async with semaphore:` below
        # have no `await` between them, so nothing else can run on this
        # single-threaded event loop in between: the decision to emit a
        # queued-thought and the eventual acquire happen on the same,
        # uninterrupted turn, with no window for another task to grab the
        # freed slot first. `asyncio.wait_for(semaphore.acquire(), 0)` looks
        # like the obvious race-free alternative but is not one:
        # `wait_for(coro, timeout<=0)` wraps the coroutine in a bare
        # `ensure_future` and checks `done()` before it has run at all, so it
        # always raises `TimeoutError`, even against a semaphore with every
        # slot free (verified against this interpreter's asyncio). `locked()`
        # carries none of that and is the correct primitive here.
        if semaphore.locked():
            await activities.emit(
                event.session_id,
                activities.thought(
                    "Several delegations are ahead of this one; I'll pick "
                    "it up as soon as a slot frees."
                ),
            )
        async with semaphore:
            if event.action == "created":
                outcome = await linear_handler.handle_created(event)
            else:
                outcome = await linear_handler.handle_prompted(event)
            logger.info("linear session %s resolved as %s", event.session_id, outcome)
    except Exception:
        logger.warning("linear session %s failed", event.session_id, exc_info=True)
