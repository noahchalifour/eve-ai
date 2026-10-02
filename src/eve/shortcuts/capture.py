"""From a specialist's inner trace to a shortcut observation.

`build_specialist` hands this the inner agent's messages after every
successful run. Nothing here calls a model; the observation is written in a
detached task (the ADR 0012 pattern), so recording never delays the answer.

A trace is shortcut-eligible when:
  1. every call in it was discovery or an allowlisted FAST_ACTION;
  2. it reduces to exactly ONE allowlisted call: if a write is present,
     reads before it (a `get_state` check) count as discovery; with no
     write, exactly one read;
  3. that call succeeded and passes its allowlist check;
  4. the turn was typed by a member: not ambient, not a routine firing
     (both carry the ambient marker), and not one that read the web.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from eve.settings import get_settings
from eve.shortcuts import allowlist
from eve.state import text_of, turn_is_ambient, turn_read_web

logger = logging.getLogger(__name__)

# A member correcting what just happened. Checked against the message that
# STARTS a turn, and applied to the previous observation on the thread.
_CORRECTION = re.compile(
    r"^\s*(no\b|nope\b|wrong\b|undo\b|not that\b|that's not\b|that is not\b|"
    r"i said\b|i meant\b|actually\b|wait\b|stop\b|put it back\b|change it back\b)",
    re.IGNORECASE,
)

_PENDING: set[asyncio.Task] = set()


@dataclass(frozen=True)
class Observation:
    specialist: str
    tool: str
    args: dict
    phrasing: str


def _calls_with_results(messages: list) -> list[tuple[str, dict, str]]:
    results = {
        m.tool_call_id: text_of(m.content) if not isinstance(m.content, str) else m.content
        for m in messages if isinstance(m, ToolMessage)
    }
    calls = []
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                calls.append((call["name"], dict(call.get("args") or {}), results.get(call.get("id"), "")))
    return calls


def _succeeded(result: str) -> bool:
    text = (result or "").strip()
    return bool(text) and not text.lower().startswith(("error", "permission denied"))


def eligible(specialist: str, inner_messages: list) -> tuple[str, dict] | None:
    """(tool, normalised args) for the one call this trace reduces to, or None."""
    calls = _calls_with_results(inner_messages)
    real = []
    for name, args, result in calls:
        if name in allowlist.DISCOVERY:
            continue
        action = allowlist.lookup(specialist, name)
        if action is None:
            return None  # a call outside the allowlist disqualifies the trace
        real.append((action, args, result))
    writes = [c for c in real if c[0].write]
    if len(writes) > 1:
        return None
    chosen = writes if writes else real
    if len(chosen) != 1:
        return None
    action, args, result = chosen[0]
    if not _succeeded(result):
        return None
    # Checked on the RAW arguments, then on the normalised ones: normalising
    # drops anything the fingerprint ignores (`data`), and a call that
    # carried brightness or volume is not the fixed call it would reduce to.
    if action.check(args) is not None:
        return None
    normalised = allowlist.normalise(action, args)
    if action.check(normalised) is not None:
        return None
    return action.tool, normalised


def last_human(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return text_of(message.content).strip()
    return ""


def is_correction(text: str) -> bool:
    return bool(_CORRECTION.match(text or ""))


def _spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


def observe(specialist: str, inner_messages: list, outer_messages: list, member: dict,
            thread_id: str | None) -> None:
    """Schedule the observation write, if this run is eligible. Never raises."""
    try:
        if not get_settings().shortcuts_enabled:
            return
        if turn_is_ambient(outer_messages) or turn_read_web(outer_messages):
            return
        found = eligible(specialist, inner_messages)
        if found is None:
            return
        tool, args = found
        _spawn(_record(member["sub"], specialist, tool, args, last_human(outer_messages)[:300], thread_id))
    except Exception:
        logger.warning("shortcut observation could not be scheduled", exc_info=True)


def note_turn_start(messages: list, member_sub: str, thread_id: str | None) -> None:
    """Called once per turn, before Eve answers: a member opening with a
    correction ("no, the other lights") retracts the previous observation
    on this thread, so a call the member had to undo never counts toward
    promotion. Detached; never raises."""
    try:
        if not get_settings().shortcuts_enabled or not thread_id:
            return
        if turn_is_ambient(messages) or not is_correction(last_human(messages)):
            return
        _spawn(_contradict(member_sub, thread_id))
    except Exception:
        logger.warning("shortcut correction could not be scheduled", exc_info=True)


async def _record(member_sub: str, specialist: str, tool: str, args: dict, phrasing: str,
                  thread_id: str | None) -> None:
    from eve.shortcuts import store

    try:
        await store.observe(member_sub, specialist, tool, args, phrasing, thread_id)
    except Exception:
        logger.warning("shortcut observation failed", exc_info=True)


async def _contradict(member_sub: str, thread_id: str) -> None:
    from eve.shortcuts import store

    try:
        await store.contradict_latest(member_sub, thread_id)
    except Exception:
        logger.warning("shortcut contradiction failed", exc_info=True)


async def drain() -> None:
    """Await every in-flight observation on this event loop. Tests, and
    nothing else."""
    loop = asyncio.get_running_loop()
    while True:
        mine = [task for task in _PENDING if task.get_loop() is loop and not task.done()]
        if not mine:
            return
        await asyncio.gather(*mine, return_exceptions=True)


def fingerprint(specialist: str, tool: str, args: dict) -> tuple[str, str | None, dict]:
    """(fixed_hash, variant value, fixed args) for one normalised call."""
    import hashlib

    action = allowlist.lookup(specialist, tool)
    fixed = dict(args)
    variant = None
    if action is not None and action.variant_key:
        raw = fixed.pop(action.variant_key, None)
        variant = None if raw is None else str(raw)
    digest = hashlib.sha256(
        json.dumps([specialist, tool, fixed], sort_keys=True, default=str).encode()
    ).hexdigest()[:32]
    return digest, variant, fixed
