"""Eve writing her own procedures.

A rule rides the REFLEX extraction pass (eve.memory.extract) because a
correction arrives as prose mid-conversation. A procedure does not: it is
multi-step, structured, and written in response to a member walking Eve
through something, so it gets a tool she calls deliberately on the CODE tier -
which has been defined and unused since Phase 1 for exactly this
(design doc section 4.2).

Storage is a `procedure`-layer eve_memory row whose `subject` is the name and
whose `content` is a SKILL.md-shaped document, so eve.skills.registry's one
parser serves both this and the files on disk.
"""

from __future__ import annotations

import logging
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from opentelemetry import trace

from eve.memory.store import add, procedure_by_name, supersede
from eve.settings import get_settings
from eve.state import EveState

logger = logging.getLogger(__name__)


def serialize_procedure(name: str, description: str, content: str) -> str:
    """SKILL.md's on-disk shape, so parse_skill_text round-trips it."""
    return f"---\nname: {name}\ndescription: {description}\n---\n{content}"


@tool
async def write_skill(
    name: str,
    description: str,
    content: str,
    state: Annotated[EveState, InjectedState],
    config: RunnableConfig,
) -> str:
    """Record a multi-step procedure you have just been taught, so you can
    follow it next time without being walked through it again. `name` is a
    short lowercase-hyphenated identifier; `description` is one sentence
    describing when the procedure applies; `content` is the steps."""
    if not get_settings().self_authoring_enabled:
        return "error: writing skills is disabled in this deployment."

    member = state["member"]
    configurable = config.get("configurable", {}) if config else {}
    try:
        existing = await procedure_by_name(member["sub"], name)
        new_id = await add(
            layer="procedure",
            scope_kind="member",
            scope_id=member["sub"],
            kind="decision",
            subject=name,
            content=serialize_procedure(name, description, content),
            source_thread=configurable.get("thread_id"),
            source_run=configurable.get("run_id"),
        )
        if existing is not None:
            await supersede(existing.id, new_id, "rewritten by write_skill")
    except Exception as exc:
        # Global constraint: a tool returns a string, never raises. A raise
        # here fails the whole turn instead of letting Eve explain.
        logger.warning("write_skill failed for %r", name, exc_info=True)
        return f"error: could not save that procedure ({exc.__class__.__name__})"

    trace.get_current_span().set_attribute("eve.authoring.procedures_written", 1)
    verb = "Updated" if existing is not None else "Saved"
    return f"{verb} the procedure {name!r}."
