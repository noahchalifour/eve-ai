"""The control loop. The only place an `idle` turn is ever classified.

WHY THIS IS NOT AN AMBIENT SOURCE. It runs on its own ~20s interval rather
than the 300s ambient tick because there is an agent waiting on the other
end of it. Five minutes of latency per conversational turn would make Eve a
worse correspondent than the member who delegated the work.

WHY IT LIVES IN EVE'S CONTAINER. The decision needs the goal, the member's
remembered preferences, and the household. eve-computer holds none of those
and is never going to (design doc: "the box learns nothing about the
family"). Only the composed prompt text crosses back.

WHY RECALL IS NOT DONE HERE. `row["context"]` is a snapshot taken once, at
dispatch. This function runs every twenty seconds per live session; a hybrid
recall per tick would be indefensible, and reusing the snapshot is what makes
running the supervisor on this side affordable at all.

ESCALATE DOES NOT RESOLVE THE SESSION. It parks it: the subprocess and the
worktrees stay up so the member's answer can resume this same session
through `send_to_coding_session`. Escalating and then discarding would throw
away the very thing the answer is for.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from eve.coding import store
from eve.models import Tier, get_model
from eve.settings import get_settings
from eve.tools_client import (
    close_coding_session,
    get_coding_session,
    kill_coding_session,
    prompt_coding_session,
)
from eve_linear import activities

logger = logging.getLogger(__name__)


class Decision(BaseModel):
    action: Literal["reply", "done", "escalate"] = Field(
        description=(
            "reply: answer the agent and let it keep working. "
            "done: the work is complete - close the session and open the pull requests. "
            "escalate: the agent needs something only the family member can answer."
        )
    )
    text: str = Field(
        description=(
            "For reply, the exact message to send to the coding agent. "
            "For done, a one-line summary for the family member. "
            "For escalate, the question to put to the family member."
        )
    )


_SYSTEM = """You delegated a coding task to a coding agent and are reading its latest turn.

Decide one of three things:
- reply: you can answer or redirect it yourself. Answer from the goal and from what you remember about the household. Be specific and brief.
- done: the work is finished. Say so.
- escalate: it needs a decision only the family member can make - a credential, a product choice, a preference you have never been told.

Prefer reply. Escalating a question you could have answered wastes the delegation. Claiming done when the work is unfinished is worse than either."""


async def decide(row: dict, turns: list[dict], pending: list[str]) -> Decision:
    transcript = "\n".join(f"{t['role']}: {t['text']}" for t in turns)
    interjections = (
        "\n\nThe family member just said this. It takes priority over your own "
        "judgement - work it into your reply:\n" + "\n".join(f"- {m}" for m in pending)
        if pending
        else ""
    )
    prompt = (
        f"{_SYSTEM}\n\n"
        f"The goal you delegated: {row['goal']}\n"
        f"Repositories: {', '.join(row['repos'])}\n\n"
        f"What you remember that might bear on this:\n{row['context']}\n\n"
        f"New turns from the agent:\n{transcript}"
        f"{interjections}"
    )
    model = get_model(Tier.CODE).with_structured_output(Decision)
    return await model.ainvoke([HumanMessage(content=prompt)])


async def tick(now: datetime | None = None) -> list[dict]:
    """Returns the sessions that resolved - finished, failed, blocked, or
    stale - on this tick. `eve_ambient.sources.coding.poll` turns each into
    a Signal."""
    now = now or datetime.now(UTC)
    settings = get_settings()
    stale_after = timedelta(minutes=settings.coding_session_stale_minutes)
    resolved: list[dict] = []

    for row in await store.live_sessions():
        try:
            outcome = await _advance(row, now, stale_after, settings)
        except Exception:
            # One session's box hiccup, one model outage, or one malformed
            # row must not stop every other session from being checked.
            logger.warning("supervising session %s raised", row["id"], exc_info=True)
            continue
        if outcome is not None:
            resolved.append(outcome)

    return resolved


def _resolved(row: dict, status: str, result: dict, now: datetime) -> dict:
    return {**row, "status": status, "result": result, "finished_at": now}


async def _emit_for(row: dict, content: dict) -> None:
    """A no-op for the common case: a chat-dispatched session has no Linear
    side and must not cost a call. Never raises - losing the narration is
    survivable, failing a running coding session is not."""
    linear_session_id = row.get("linear_session_id")
    if not linear_session_id:
        return
    try:
        await activities.emit(linear_session_id, content, session_id=row["id"])
    except Exception:
        logger.warning(
            "emitting to linear for session %s raised", row["id"], exc_info=True
        )


async def _heartbeat(row: dict, box: dict, now, settings) -> None:
    """Linear marks a session stale after 30 minutes of silence, and a coding
    agent can work far longer than that without producing a decision. The
    state is recoverable (a later activity un-stales it), so this is about
    not inviting a human to intervene in work that is going fine."""
    if not row.get("linear_session_id"):
        return
    last = row.get("linear_emitted_at") or row.get("created_at")
    if last is None:
        return
    if (now - last) < timedelta(minutes=settings.linear_heartbeat_minutes):
        return
    latest = "; ".join((box.get("activity") or [])[-1:]) or "still working"
    await _emit_for(row, activities.thought(f"Still on it: {latest}"))


async def _advance(row: dict, now, stale_after, settings) -> dict | None:
    # The outermost bound, checked before anything else so an expired
    # session cannot spend one more model call on its way out. A session
    # parked on `blocked` is running nothing the box could time out; it is
    # waiting on a human, and some humans never answer.
    age = (now - row["created_at"]).total_seconds()
    if age > settings.coding_session_timeout_seconds:
        await kill_coding_session(row["id"])
        result = {"error": f"the session ran too long ({int(age)}s) and was stopped"}
        await store.mark_resolved(row["id"], "failed", result)
        await _emit_for(row, activities.error(result["error"]))
        return _resolved(row, "failed", result, now)

    box = await get_coding_session(row["id"], since=row["cursor"])

    if box is None:
        if now - row["updated_at"] > stale_after:
            await store.mark_resolved(row["id"], "stale", {})
            await _emit_for(
                row, activities.error("the session went quiet and never reported back")
            )
            return _resolved(row, "stale", {}, now)
        return None

    status = box.get("status")
    if status == "failed":
        result = {"error": box.get("error") or "the session failed"}
        await store.mark_resolved(row["id"], "failed", result)
        await _emit_for(row, activities.error(result["error"]))
        return _resolved(row, "failed", result, now)
    if status == "killed":
        await store.mark_resolved(row["id"], "failed", {"error": "the session was killed"})
        await _emit_for(row, activities.error("the session was killed"))
        return _resolved(row, "failed", {"error": "the session was killed"}, now)
    if status != "idle":
        # The only place a session is doing work with no decision to make,
        # which is exactly where a long Linear silence would strand it.
        await _heartbeat(row, box, now, settings)
        return None

    pending = box.get("pending") or []
    # A blocked session is waiting on a human. Re-deciding every twenty
    # seconds would be a notification loop, not a conversation - so it only
    # wakes when the member actually says something.
    if row["status"] == "blocked" and not pending:
        return None

    turns = box.get("turns") or []
    if not turns and not pending:
        return None

    count = await store.bump_supervisor_turns(row["id"])
    if count > settings.coding_max_supervisor_turns:
        # graph.py's _LOOP_EXHAUSTED, one level out: whatever the budget is,
        # a loop that blows it has to answer in English rather than stall.
        question = (
            "I've been going back and forth with the coding agent on this "
            f"without reaching an answer: {row['goal']}. Could you take a look?"
        )
        await store.set_status(row["id"], "blocked")
        return _resolved(row, "blocked", {"question": question}, now)

    decision = await decide(row, turns, pending)
    await store.advance_cursor(row["id"], box.get("cursor", row["cursor"]))

    if decision.action == "reply":
        sent = await prompt_coding_session(row["id"], decision.text, kind="reply")
        if sent.startswith("error:"):
            logger.warning("could not deliver a reply to session %s", row["id"])
            return None
        await store.set_status(row["id"], "running")
        await _emit_for(
            row, activities.action("Working", row["goal"], decision.text)
        )
        return None

    if decision.action == "escalate":
        await store.set_status(row["id"], "blocked")
        await _emit_for(row, activities.elicitation(decision.text))
        return _resolved(row, "blocked", {"question": decision.text}, now)

    closed = await close_coding_session(row["id"]) or {"prs": []}
    result = {"summary": decision.text, **closed}
    await store.mark_resolved(row["id"], "finished", result)
    prs = [pr for pr in result.get("prs", []) if pr.get("pr_url")]
    links = "; ".join(f"{pr['repo']}: {pr['pr_url']}" for pr in prs)
    await _emit_for(
        row,
        activities.response(
            f"{decision.text} Pull requests: {links}" if links else
            f"{decision.text} No changes, so there's no pull request."
        ),
    )
    return _resolved(row, "finished", result, now)
