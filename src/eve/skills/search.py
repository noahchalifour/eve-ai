"""search_skills: the one tool that turns Eve's fixed toolset into an
extensible one. A SKILL.md match returns a procedure directly as the tool's
result - knowledge, not a new capability, so nothing about the bound-tool
list changes. An MCP match is different: it appends a DynamicToolSpec to
state via a Command update, materialized into a real callable on the next
model call (eve.skills.materialize, Task 11; wired into the graph in Task
13) - never a live callable itself, because Aegra checkpoints EveState to
Postgres across every turn (design doc section 5.1).
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from opentelemetry import trace

from eve.memory.embed import embed_query
from eve.memory.store import load_procedures
from eve.settings import get_settings
from eve.skills.mcp_registry import registered_mcp_tools
from eve.skills.registry import Skill, load_skills
from eve.state import EveState


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def rank_skills(query: str, skills: list[Skill], top_k: int = 3) -> list[Skill]:
    if not skills:
        return []
    query_vec = await embed_query(query)
    scored = [
        (_dot(query_vec, await embed_query(skill.description or skill.name)), skill)
        for skill in skills
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [skill for _, skill in scored[:top_k]]


@tool
async def search_skills(
    query: str,
    state: Annotated[EveState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Search for a known procedure or a newly-available tool matching a
    request outside your normal toolset."""
    # Design doc section 10: "is search_skills ever called, or is the whole
    # mechanism unused" and "how many dynamically-bound tools accumulate"
    # are both questions this attribute pair exists to answer with a number.
    trace.get_current_span().set_attribute("eve.skills.search_used", True)
    # Authored procedures come from the database; filesystem SKILL.md files
    # and MCP metadata come from the registry. Indistinguishable downstream.
    #
    # Gated on the setting for the reason load_always_on's docstring gives for
    # rules: with self-authoring off, rows written during an earlier enabled
    # period must stop applying, not merely stop being written. It also spares
    # every skills search a round trip Phase 4 never paid.
    authored = []
    if get_settings().self_authoring_enabled:
        try:
            authored = await load_procedures(state["member"]["sub"])
        except Exception:
            # A skills search that cannot reach Postgres should still return
            # the filesystem corpus rather than failing the turn.
            authored = []
    skills = load_skills(mcp_tools=registered_mcp_tools(), authored=authored)
    matches = await rank_skills(query, skills)
    if not matches:
        return Command(
            update={
                "messages": [
                    ToolMessage("No matching skill or tool found.", tool_call_id=tool_call_id)
                ]
            }
        )

    procedures = [m for m in matches if m.kind == "procedure"]
    mcp_matches = [m for m in matches if m.kind == "mcp_tool" and m.spec]

    existing = state.get("dynamic_tools", [])
    new_specs = [m.spec for m in mcp_matches if m.spec not in existing]
    cap = get_settings().dynamic_tools_cap
    merged = (existing + new_specs)[-cap:]
    trace.get_current_span().set_attribute("eve.skills.mcp_bound", len(merged))

    lines = [f"# {m.name}\n{m.content}" for m in procedures]
    lines += [f"Tool available: {m.name} - {m.description}" for m in mcp_matches]
    content = "\n\n".join(lines)

    return Command(
        update={
            "messages": [ToolMessage(content, tool_call_id=tool_call_id)],
            "dynamic_tools": merged,
        }
    )
