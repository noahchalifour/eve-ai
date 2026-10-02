"""Reminders (ENG-372): one-shot routines delivered verbatim.

Built on `eve_routine` rather than a second scheduler: a reminder is a row
with `kind = 'reminder'` and `cadence = {"once_at": <aware ISO>}`. The
ambient routines source claims it like any routine; `eve_ambient.notify`
pushes the message as written, with no thread and no model call, and the row
is then `expired`.

Same guards as `eve.routines.tools`, in the same order: refuse an ambient
turn first (a routine may not mint reminders), validate, check the grant,
then touch storage.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from eve.general import timeutil
from eve.routines import cadence as cadence_rules, store
from eve.routines.tools import PERMISSION, _refuse_ambient
from eve.settings import get_settings
from eve.specialists.permissions import permission_denial
from eve.state import WEB_AUTHORING_REFUSAL, EveState, turn_is_ambient, turn_read_web

logger = logging.getLogger(__name__)

KIND = "reminder"


def _member(config: RunnableConfig) -> dict:
    return (config.get("configurable") or {}).get("member") or {}


def _local(moment: datetime, tz_name: str) -> str:
    tz = timeutil.zone(None, tz_name)
    return moment.astimezone(tz).strftime("%a %b %-d at %-I:%M %p %Z")


@tool
async def set_reminder(
    message: str,
    at: str,
    state: Annotated[EveState, InjectedState],
    config: RunnableConfig,
) -> str:
    """Remind the member of something once, at a specific time. The message
    is pushed to them exactly as you write it, so phrase it as the reminder
    itself ("Take the laundry out"). `at` is when, in their own timezone:
    "in 20 minutes", "tomorrow at 9am", "friday 17:30", or ISO-8601.
    For anything recurring use schedule_routine instead."""
    member = _member(config)
    if turn_is_ambient(state.get("messages") or []):
        return _refuse_ambient(member)
    if turn_read_web(state.get("messages") or []):
        return WEB_AUTHORING_REFUSAL

    text = (message or "").strip()
    if not text:
        return "The reminder needs a message."
    limit = get_settings().routine_max_instruction_chars
    if len(text) > limit:
        return f"The reminder is too long: {len(text)} characters, the limit is {limit}."

    tz_name = member.get("timezone") or "UTC"
    now = datetime.now(UTC)
    try:
        moment = timeutil.parse_moment(at, timeutil.zone(None, tz_name), now)
    except timeutil.TimeError as exc:
        return f"That time was rejected: {exc}."
    cadence = {"once_at": moment.isoformat()}
    error = cadence_rules.validate_at(cadence, now)
    if error is not None:
        return f"That time was rejected: {error}."

    denial = permission_denial(member.get("permissions") or [], PERMISSION)
    if denial is not None:
        return denial

    title = text if len(text) <= get_settings().routine_max_title_chars else (
        text[: get_settings().routine_max_title_chars - 1].rstrip() + "…"
    )
    try:
        await store.create(
            member_sub=member["sub"],
            title=title,
            instruction=text,
            cadence=cadence,
            timezone=tz_name,
            next_run_at=moment.astimezone(UTC),
            expires_at=None,
            kind=KIND,
        )
    except Exception as exc:
        logger.warning("set_reminder failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"
    return f"Reminder set for {_local(moment, tz_name)}: \"{text}\"."


@tool
async def list_reminders(
    state: Annotated[EveState, InjectedState],
    config: RunnableConfig,
) -> str:
    """List the member's upcoming reminders."""
    member = _member(config)
    if turn_is_ambient(state.get("messages") or []):
        return _refuse_ambient(member)
    denial = permission_denial(member.get("permissions") or [], PERMISSION)
    if denial is not None:
        return denial
    try:
        rows = await store.list_for(member["sub"], kind=KIND)
    except Exception as exc:
        logger.warning("list_reminders failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"
    upcoming = [row for row in rows if row["status"] == "active"]
    if not upcoming:
        return "No reminders are set."
    upcoming.sort(key=lambda row: row["cadence"].get("once_at", ""))
    tz_name = member.get("timezone") or "UTC"
    return "\n".join(
        f"- {_local(datetime.fromisoformat(row['cadence']['once_at']), tz_name)}: \"{row['instruction']}\""
        for row in upcoming
    )


@tool
async def cancel_reminder(
    reference: str,
    state: Annotated[EveState, InjectedState],
    config: RunnableConfig,
) -> str:
    """Cancel an upcoming reminder. `reference` is part of its message, as
    the member said it."""
    member = _member(config)
    if turn_is_ambient(state.get("messages") or []):
        return _refuse_ambient(member)
    denial = permission_denial(member.get("permissions") or [], PERMISSION)
    if denial is not None:
        return denial
    try:
        matches = [
            row for row in await store.find_by_title(member["sub"], reference, kind=KIND)
            if row["status"] == "active"
        ]
    except Exception as exc:
        logger.warning("cancel_reminder lookup failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"
    if not matches:
        return f"No upcoming reminder matches {reference!r}."
    if len(matches) > 1:
        titles = ", ".join(f"\"{row['title']}\"" for row in matches)
        return f"{reference!r} matches more than one reminder ({titles}). Nothing was cancelled; ask which one."
    row = matches[0]
    try:
        removed = await store.delete(member["sub"], row["id"])
    except Exception as exc:
        logger.warning("cancel_reminder failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"
    if not removed:
        return f"No upcoming reminder matches {reference!r}."
    return f"Cancelled the reminder \"{row['title']}\"."
