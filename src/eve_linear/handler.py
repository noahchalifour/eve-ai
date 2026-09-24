"""Acknowledge, gate, dispatch. The ordering here IS the contract.

THE TEN SECOND RULE. Linear marks a session unresponsive if no activity
arrives within ten seconds of `created`. A dispatch does a hybrid memory
recall and an HTTP call to eve-computer, so neither may precede the first
emission. The three gate checks DO precede it, because all three are pure
local computation (a dict lookup, a frozenset test, a string scan) and their
result decides which activity to emit. So the acknowledgement is always
exactly one GraphQL call, and nothing that can block on a network or a
database runs before it.

`tests/test_linear_handler.py` asserts that ordering directly, because it is
invisible in the code once written and a well-meaning refactor will break it.

WHY THE ROW IS WRITTEN AFTER THE BOX ACCEPTS. Same reason
`eve.coding.dispatch` does it: a row for a session the box never heard of is
polled forever by the supervisor and eventually reported as stale, for work
that never started.
"""

from __future__ import annotations

import logging
import uuid

from eve.coding import catalogue, store
from eve.coding.dispatch import _recall_context
from eve.settings import get_settings
from eve.tools_client import (
    create_coding_session,
    invoke,
    kill_coding_session,
    prompt_coding_session,
)
from eve_linear import activities
from eve_linear.identity import resolve_member, resolve_repos
from eve_linear.types import LinearEvent

logger = logging.getLogger(__name__)

_ASSISTANT = "eve"


def _guidance_text(raw: object) -> str:
    """Linear sends `guidance` as a list of `{origin, body}` objects, one per
    scope (workspace, parent team, team), not as a string.

    `resolve_repos` runs a regex over this, and a regex over a list raises
    TypeError inside the webhook's background task - which surfaces as a
    session that emits nothing at all and goes stale, the least diagnosable
    failure this path has. Every shape therefore has to come out as text:
    a bare string passes through, a list contributes each entry's `body`,
    and anything unrecognised contributes nothing rather than raising.
    """
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, list):
        return ""
    parts: list[str] = []
    for entry in raw:
        if isinstance(entry, str):
            parts.append(entry)
        elif isinstance(entry, dict):
            body = entry.get("body")
            if isinstance(body, str):
                parts.append(body)
    return "\n".join(parts)


def parse_event(payload: dict) -> LinearEvent:
    """Total over every payload shape Linear sends. Absent nesting becomes
    None rather than a KeyError, because a mention in a document is a real
    event this feature refuses rather than crashes on.

    `guidance` and `promptContext` are TOP-LEVEL on the AgentSessionEvent
    payload, not inside `agentSession` - verified against a live delivery.
    Reading them from the session left both silently empty, so every
    delegation refused for want of a repo and every goal arrived blank. The
    session is still checked as a fallback in case Linear moves them.
    """
    session = payload.get("agentSession") or {}
    issue = session.get("issue") or {}
    team = issue.get("team") or {}
    creator = session.get("creator") or session.get("actor") or {}
    activity = payload.get("agentActivity") or {}
    content = activity.get("content") or {}
    return LinearEvent(
        action=payload.get("action") or "",
        session_id=session.get("id"),
        issue_id=issue.get("id"),
        team_id=team.get("id"),
        actor_id=creator.get("id"),
        guidance=_guidance_text(
            payload.get("guidance") or session.get("guidance")
        ),
        prompt_body=content.get("body") or "",
        prompt_context=(
            payload.get("promptContext") or session.get("promptContext") or ""
        ),
    )


async def _create_thread(member_sub: str) -> str | None:
    """A Linear session gets an Aegra thread too, so the family still gets
    the push notification and a thread to talk in when it resolves. Isolated
    into its own function so the handler's tests can replace it without an
    Aegra client.

    The ambient token only authenticates alongside `x-eve-on-behalf-of`
    (see `eve.auth`), which is also what makes the thread the delegating
    member's rather than nobody's - the same pairing `eve_ambient.notify`
    uses.

    Degrades to None on failure rather than raising: `handle_created` already
    treats a None thread_id as a failure to emit as an `error` activity, and
    letting an Aegra outage raise here instead would produce exactly the
    silence-after-acknowledgement the spec's failure modes section calls the
    worst outcome.
    """
    from langgraph_sdk import get_client

    settings = get_settings()
    client = get_client(
        url=settings.ambient_aegra_base_url,
        headers={
            "Authorization": f"Bearer {settings.ambient_token}",
            "x-eve-on-behalf-of": member_sub,
        },
    )
    try:
        thread = await client.threads.create(metadata={"linear": True})
        return thread["thread_id"]
    except Exception:
        logger.warning("could not create an aegra thread for a linear session", exc_info=True)
        return None


async def _move_issue_to_started(issue_id: str, team_id: str) -> dict:
    result = await invoke(
        "linear.move_issue_to_started", {"issue_id": issue_id, "team_id": team_id}
    )
    return {"success": not (isinstance(result, str) and result.startswith("error:"))}


async def handle_created(event: LinearEvent) -> str:
    settings = get_settings()

    member, refusal = resolve_member(event.actor_id or "")
    repos: list[str] = []
    if refusal is None:
        repos, refusal = resolve_repos(event.guidance, settings.linear_repo_allowlist)

    if refusal is not None:
        # An elicitation for the answerable refusal, an error for the two
        # that no reply in Linear can fix.
        content = (
            activities.elicitation(refusal.message)
            if refusal.kind == "no_repo"
            else activities.error(refusal.message)
        )
        await activities.emit(event.session_id, content)
        return f"refused:{refusal.kind}"

    goal = event.prompt_context or event.prompt_body
    await activities.emit(
        event.session_id,
        activities.thought(f"Picking this up now. Working in {', '.join(repos)}."),
    )

    context = await _recall_context(goal, member.sub)
    thread_id = await _create_thread(member.sub)
    if not thread_id:
        await activities.emit(
            event.session_id,
            activities.error("I couldn't open a thread to track this. Nothing started."),
        )
        return "failed"

    session_id = str(uuid.uuid4())
    agent = settings.coding_default_agent
    # `model` is NOT NULL on the row, and nobody in Linear named one.
    # `catalogue.validate(None, agent)` resolves the agent's fallback rather
    # than raising, which is exactly this case.
    model = await catalogue.validate(None, agent)
    dispatched = await create_coding_session(
        session_id, agent, model, repos, goal
    )
    if isinstance(dispatched, str) and dispatched.startswith("error:"):
        await activities.emit(
            event.session_id,
            activities.error(f"I couldn't start the coding session: {dispatched}"),
        )
        return "failed"

    try:
        await store.create_session(
            session_id=session_id,
            member_sub=member.sub,
            thread_id=thread_id,
            goal=goal,
            agent=agent,
            model=model,
            repos=repos,
            context=context,
            linear_session_id=event.session_id,
            linear_issue_id=event.issue_id,
        )
    except Exception:
        # Almost certainly the unique index on linear_session_id: Linear
        # retried, and another attempt already owns this agent session. The
        # box is now running a session with no row, so stop it rather than
        # leaving an orphan burning tokens.
        logger.info(
            "linear session %s already has a coding session; not duplicating",
            event.session_id,
        )
        await kill_coding_session(session_id)
        return "duplicate"

    if event.issue_id and event.team_id:
        # Best-effort, and deliberately after the row exists: the work is
        # already underway, and a board that did not move is cosmetic.
        await _move_issue_to_started(event.issue_id, event.team_id)

    return "dispatched"


async def handle_prompted(event: LinearEvent) -> str:
    """A human answering an elicitation, or interjecting mid-run.

    `escalate` parks a session rather than resolving it precisely so this can
    resume the same one, with its subprocess and worktrees intact.
    """
    row = await store.get_by_linear_session(event.session_id or "")
    if row is None:
        logger.info("prompted for an unknown linear session %s", event.session_id)
        return "unknown-session"

    if row["status"] in ("finished", "failed", "stale"):
        await activities.emit(
            event.session_id,
            activities.error(
                "That session has already finished. Delegate the issue again "
                "and I'll start a fresh one."
            ),
            session_id=row["id"],
        )
        return "already-resolved"

    sent = await prompt_coding_session(
        row["id"], event.prompt_body, kind="interjection"
    )
    if isinstance(sent, str) and sent.startswith("error:"):
        await activities.emit(
            event.session_id,
            activities.error("I couldn't pass that on to the coding agent."),
            session_id=row["id"],
        )
        return "failed"

    if row["status"] == "blocked":
        await store.set_status(row["id"], "running")
    return "resumed"
