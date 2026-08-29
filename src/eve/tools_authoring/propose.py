"""Proposing a tool, and the one human gate in the program.

The gate is LangGraph's `interrupt()`: the run pauses, Aegra checkpoints it,
and the operator resumes with Command(resume={"approved": bool, "why": str})
from the Agent Chat UI or the SDK. No approval UI is built here.

`tools.author` is required to propose, which collapses proposer and approver
into one person deliberately (design section 5.1): the alternative is a
notification-and-queue workflow system for a household of five, serving a case
- someone who cannot approve code wanting code written - that is not worth it.
One consequence matters: the interrupt always surfaces in a thread owned by
someone entitled to answer it.
"""

from __future__ import annotations

import logging
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from langgraph.types import interrupt
from opentelemetry import trace

from eve.settings import get_settings
from eve.specialists.permissions import permission_denial
from eve.state import EveState
from eve.tools_authoring.inspect import check
from eve.tools_authoring.store import approve as store_approve
from eve.tools_authoring.store import propose as store_propose
from eve.tools_authoring.store import reject as store_reject

logger = logging.getLogger(__name__)

# materialize.py maps only these and silently falls back to str for anything
# else. Refusing beats inheriting that wrong validation.
_MAPPED_TYPES = frozenset({"string", "integer", "number", "boolean"})


def _unmapped_types(args_schema: dict) -> list[str]:
    return sorted(
        {
            info.get("type", "string")
            for info in (args_schema.get("properties") or {}).values()
            if info.get("type", "string") not in _MAPPED_TYPES
        }
    )


@tool
async def propose_tool(
    name: str,
    description: str,
    args_schema: dict,
    source: str,
    state: Annotated[EveState, InjectedState],
    config: RunnableConfig,
) -> str:
    """Propose a small Python tool for a calculation or parse you keep doing
    by hand. `source` must define exactly one function,
    `def run(arguments: dict) -> dict`. It runs with NO network, NO file
    access and NO credentials, so it can only compute over what it is handed.
    A human must approve it before it can ever run."""
    settings = get_settings()
    if not settings.sandbox_enabled:
        return "error: proposing tools is disabled in this deployment."

    member = state["member"]
    denial = permission_denial(member.get("permissions") or [], "tools.author")
    if denial is not None:
        return denial

    span = trace.get_current_span()
    result = check(source)
    if not result.ok:
        span.set_attribute("eve.sandbox.ast_rejected", 1)
        problems = "\n".join(f"- {p}" for p in result.problems)
        return (
            "That source cannot be accepted as written:\n"
            f"{problems}\n"
            "A sandbox tool is a pure function over the data it is handed."
        )

    unmapped = _unmapped_types(args_schema)
    if unmapped:
        return (
            f"Argument types {unmapped} are not supported. Use only string, "
            "integer, number, or boolean properties."
        )

    configurable = config.get("configurable", {}) if config else {}
    try:
        tool_id = await store_propose(
            name=name,
            description=description,
            args_schema=args_schema,
            source=source,
            proposed_by=member["sub"],
            thread_id=configurable.get("thread_id"),
            run_id=configurable.get("run_id"),
        )
    except Exception as exc:
        logger.warning("could not record the proposal %r", name, exc_info=True)
        return f"error: could not record that proposal ({exc.__class__.__name__})"

    span.set_attribute("eve.sandbox.proposed", 1)

    # The gate. Everything the approver needs is in this payload: reading the
    # source elsewhere is how a wrong version gets approved.
    decision = interrupt(
        {
            "kind": "tool_approval",
            "tool_id": tool_id,
            "name": name,
            "description": description,
            "args_schema": args_schema,
            "source": source,
            "imports": result.imports,
            "requested_by": member["sub"],
            "thread_id": configurable.get("thread_id"),
        }
    )

    approved = bool((decision or {}).get("approved"))
    why = (decision or {}).get("why") or "unspecified"
    try:
        if approved:
            await store_approve(tool_id, member["sub"])
        else:
            await store_reject(tool_id, why)
    except Exception as exc:
        logger.warning("could not record the decision for %r", name, exc_info=True)
        return f"error: could not record that decision ({exc.__class__.__name__})"

    span.set_attribute("eve.sandbox.approved" if approved else "eve.sandbox.rejected", 1)
    if approved:
        return f"{name!r} was approved and is now available."
    return f"{name!r} was not approved ({why})."
