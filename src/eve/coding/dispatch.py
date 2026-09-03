"""Eve's three coding tools. Permission is checked here, before the HTTP
call, so a denied request never reaches eve-computer at all - ADR 0006's
pattern, applied a fourth time.

WHY THE ROW IS WRITTEN AFTER THE BOX ACCEPTS. A row for a session the box
never heard of would be polled forever by the supervisor and eventually
reported to the member as stale, for work that never started. Order
matters; `dispatch_computer_task` does the same thing for the same reason.

WHY RECALL HAPPENS HERE AND ONLY HERE. The supervisor runs every ~20s and
needs household context to answer the agent's questions - that is the whole
reason it lives in Eve's container and not on the box. A hybrid recall per
tick would be indefensible, so it is taken once, now, and snapshotted onto
the row for every later supervisor call to reuse.
"""

from __future__ import annotations

import logging
import uuid

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from eve.coding import catalogue, store
from eve.memory import store as memory_store
from eve.settings import get_settings
from eve.specialists.permissions import permission_denial
from eve.tools_client import (
    create_coding_session,
    get_coding_session,
    prompt_coding_session,
)

logger = logging.getLogger(__name__)

PERMISSION = "code.delegate"
AGENTS = ("codex", "claude", "opencode")


async def _recall_context(goal: str, member_sub: str) -> str:
    """One recall, at creation, snapshotted onto the row for every later
    supervisor call to reuse.

    NOT `eve.memory.recall.recall` - that is a graph node taking (state,
    config), not a query function. The two primitives underneath it are what
    is wanted here: always-on memory carries the member's authored rules
    (which is how "use Claude Code for anything touching the graph" reaches
    this decision), and the lexical episodic arm cannot fail and needs no
    network call, unlike the vector arm.

    Degrades to empty rather than failing the dispatch: a session with no
    remembered preferences is worse than one with them, and far better than
    no session at all.
    """
    try:
        profile, household, _digest, rules = await memory_store.load_always_on(
            member_sub, None, include_rules=True
        )
        episodic = await memory_store.search_episodic_lexical(member_sub, goal, limit=10)
        lines = [
            m.content
            for m in (*rules, *profile, *household, *episodic)
            if getattr(m, "content", None)
        ]
        return "\n".join(lines)
    except Exception:
        logger.warning("recall for a coding session failed", exc_info=True)
        return ""


@tool
async def delegate_coding_task(
    repos: list[str],
    goal: str,
    config: RunnableConfig,
    agent: str | None = None,
    model: str | None = None,
) -> str:
    """Delegate a coding task to a dedicated coding agent working in a real
    git checkout on Eve's computer. Use for changes to source code that
    should end in a pull request.

    `repos` is one or more GitHub repositories ("owner/name" or just "name");
    pass several for a change that spans repositories, and they will share a
    branch name and produce one pull request each.

    `agent` chooses the harness: "codex" (rides the ChatGPT subscription,
    the default when nothing points elsewhere), "claude" (the strongest
    coder, metered spend), or "opencode". `model` is any model the LiteLLM
    proxy serves - pick a small fast one for a small change and a strong one
    for a hard change, and honour whatever the member has said they prefer.

    Returns immediately; the result is reported later, in a separate message.
    """
    member = config["configurable"]["member"]
    denial = permission_denial(member.get("permissions", []), PERMISSION)
    if denial:
        return denial

    thread_id = config.get("configurable", {}).get("thread_id")
    if not thread_id:
        return "error: no thread to report the result on"
    if not repos:
        return "error: a coding session needs at least one repo to work in"

    chosen_agent = agent or get_settings().coding_default_agent
    if chosen_agent not in AGENTS:
        return f"error: unknown agent {chosen_agent!r}; expected one of {', '.join(AGENTS)}"

    chosen_model = await catalogue.validate(model, chosen_agent)
    session_id = str(uuid.uuid4())
    context = await _recall_context(goal, member["sub"])

    dispatched = await create_coding_session(
        session_id, chosen_agent, chosen_model, repos, goal
    )
    if dispatched.startswith("error:"):
        return dispatched

    await store.create_session(
        session_id=session_id,
        member_sub=member["sub"],
        thread_id=thread_id,
        goal=goal,
        agent=chosen_agent,
        model=chosen_model,
        repos=repos,
        context=context,
    )
    return "I'm on it — I'll let you know when it's done."


@tool
async def check_coding_session(config: RunnableConfig) -> str:
    """What Eve's delegated coding sessions are doing right now. Use when a
    member asks how something is going, or before sending a message into a
    session, since this is where the session ids come from."""
    member = config["configurable"]["member"]
    denial = permission_denial(member.get("permissions", []), PERMISSION)
    if denial:
        return denial

    sessions = await store.live_sessions_for(member["sub"])
    if not sessions:
        return "No coding sessions are running."

    lines = []
    for row in sessions:
        live = await get_coding_session(row["id"]) or {}
        activity = "; ".join(live.get("activity", [])[-3:]) or "no activity yet"
        lines.append(
            f"- {row['id'][:8]} ({row['agent']}/{row['model']}) on "
            f"{', '.join(row['repos'])}: {row['goal']} — {row['status']}. {activity}"
        )
    return "\n".join(lines)


@tool
async def send_to_coding_session(
    session_id: str, message: str, config: RunnableConfig
) -> str:
    """Pass a member's message into a running coding session - a correction,
    a preference, an answer. Get `session_id` from check_coding_session. The
    message is delivered at the end of the agent's current turn, not
    mid-turn."""
    member = config["configurable"]["member"]
    denial = permission_denial(member.get("permissions", []), PERMISSION)
    if denial:
        return denial

    row = await store.get(session_id)
    if row is None or row["member_sub"] != member["sub"]:
        # Deliberately one message for both cases: whether another member
        # has a session by this id is not this member's business.
        return "I don't have a session by that id."

    sent = await prompt_coding_session(session_id, message, kind="interjection")
    if sent.startswith("error:"):
        return sent
    return "I'll pass that on at the end of its current step."
