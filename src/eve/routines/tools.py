"""The only way a routine is created, listed, or destroyed.

Checks run in the same order `eve.widgets.tools` uses, and for the same
reason: refuse the turn before validating the input, validate the input
before checking the grant, check the grant before touching storage. The
cheapest and most categorical refusals come first.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from eve.routines import cadence as cadence_rules, store
from eve.settings import get_settings
from eve.specialists.permissions import permission_denial
from eve.state import EveState, turn_is_ambient

logger = logging.getLogger(__name__)

PERMISSION = "routines"

_AMBIENT_REFUSAL = (
    "A routine cannot create, change, or cancel a routine. Only {name} can, "
    "by asking me directly."
)

_SCHEDULE_DESCRIPTION = """Set up a standing request you will carry out on a schedule.

Use this when the member wants something checked or done repeatedly ("every
morning", "keep an eye on", "let me know if"), not for a one-off answer.

`instruction` is what you will read back to yourself on every run, so write it
as a complete standing request in the member's own terms, including anything
you will need that this conversation established. You will not see this
conversation again.

`cadence` is exactly one of:
  {"every_hours": <1 to 168>}
  {"daily_at": "HH:MM"}
  {"weekly_at": {"day": "monday".."sunday", "time": "HH:MM"}}

Nothing may run more often than hourly. Times are in the member's own
timezone.

`expires_at` is an optional ISO-8601 timestamp. Set it whenever the request
has a natural horizon ("flights in February"), so the routine stops on its own
instead of running forever."""


def _member(config: RunnableConfig) -> dict:
    return (config.get("configurable") or {}).get("member") or {}


def _refuse_ambient(member: dict) -> str:
    return _AMBIENT_REFUSAL.format(name=member.get("name") or "a family member")


@tool(description=_SCHEDULE_DESCRIPTION)
async def schedule_routine(
    title: str,
    instruction: str,
    cadence: dict,
    state: Annotated[EveState, InjectedState],
    config: RunnableConfig,
    expires_at: str | None = None,
) -> str:
    member = _member(config)
    settings = get_settings()

    # First, and categorically: a routine firing is an ambient turn, and an
    # ambient turn may not create a durable resource in a member's account.
    # This is what makes a fork bomb unreachable.
    if turn_is_ambient(state.get("messages") or []):
        return _refuse_ambient(member)

    error = cadence_rules.validate(cadence)
    if error is not None:
        return f"That schedule was rejected: {error}."

    if len(title) > settings.routine_max_title_chars:
        return (
            f"The routine title is too long: {len(title)} characters, "
            f"the limit is {settings.routine_max_title_chars}."
        )
    if len(instruction) > settings.routine_max_instruction_chars:
        return (
            f"The instruction is too long: {len(instruction)} characters, "
            f"the limit is {settings.routine_max_instruction_chars}."
        )

    expiry = None
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at)
        except ValueError:
            return f"expires_at must be ISO-8601, got {expires_at!r}."
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)

    denial = permission_denial(member.get("permissions") or [], PERMISSION)
    if denial is not None:
        return denial

    timezone = member.get("timezone") or "UTC"
    now = datetime.now(UTC)
    try:
        first = cadence_rules.next_after(cadence, timezone, now)
        row = await store.create(
            member_sub=member["sub"],
            title=title,
            instruction=instruction,
            cadence=cadence,
            timezone=timezone,
            next_run_at=first,
            expires_at=expiry,
        )
    except Exception as exc:
        logger.warning("schedule_routine failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"

    ending = f", until {expiry.date().isoformat()}" if expiry else ""
    return (
        f"Scheduled \"{row['title']}\" {cadence_rules.describe(cadence)}"
        f"{ending}. First run {first.isoformat()}."
    )


@tool
async def list_routines(
    state: Annotated[EveState, InjectedState],
    config: RunnableConfig,
) -> str:
    """List the standing requests you are already running for this member.

    Use this whenever they ask what you are watching, tracking, or checking
    for them."""
    member = _member(config)

    if turn_is_ambient(state.get("messages") or []):
        return _refuse_ambient(member)

    denial = permission_denial(member.get("permissions") or [], PERMISSION)
    if denial is not None:
        return denial

    try:
        rows = await store.list_for(member["sub"])
    except Exception as exc:
        logger.warning("list_routines failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"

    if not rows:
        return "No routines are set up right now."

    lines = []
    for row in rows:
        last = row.get("last_outcome")
        seen = (
            f"last run {row['last_run_at'].isoformat()} ({last})"
            if row.get("last_run_at")
            else "not run yet"
        )
        state_note = "" if row["status"] == "active" else f" [{row['status']}]"
        lines.append(
            f"- \"{row['title']}\"{state_note}: "
            f"{cadence_rules.describe(row['cadence'])}, {seen}. "
            f"{row['instruction']}"
        )
    return "\n".join(lines)


@tool
async def cancel_routine(
    reference: str,
    state: Annotated[EveState, InjectedState],
    config: RunnableConfig,
) -> str:
    """Stop a standing request permanently. `reference` is the routine's title
    or part of it, as the member said it."""
    member = _member(config)

    if turn_is_ambient(state.get("messages") or []):
        return _refuse_ambient(member)

    denial = permission_denial(member.get("permissions") or [], PERMISSION)
    if denial is not None:
        return denial

    try:
        matches = await store.find_by_title(member["sub"], reference)
    except Exception as exc:
        logger.warning("cancel_routine lookup failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"

    if not matches:
        return f"No routine matches {reference!r}."
    if len(matches) > 1:
        titles = ", ".join(f"\"{row['title']}\"" for row in matches)
        return (
            f"{reference!r} matches more than one routine ({titles}). "
            "Nothing was cancelled; ask which one they mean."
        )

    row = matches[0]
    try:
        removed = await store.delete(member["sub"], row["id"])
    except Exception as exc:
        logger.warning("cancel_routine failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"

    if not removed:
        return f"No routine matches {reference!r}."
    return f"Cancelled \"{row['title']}\". It will not run again."
