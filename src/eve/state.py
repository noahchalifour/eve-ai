"""Graph state. `permissions` is resolved in Phase 1 and consumed in Phase 3;
carrying it now means the tools loop does not reshape state when it lands."""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages

from eve.memory.types import MemoryBundle
from eve.skills.types import DynamicToolSpec


def _replace_dynamic_tools(
    _old: list[DynamicToolSpec], new: list[DynamicToolSpec]
) -> list[DynamicToolSpec]:
    """Last-write-wins. A reducer is what gives a channel a default: without
    one LangGraph uses `LastValue`, which holds no value at all until
    something writes it, so on a fresh thread the key is simply absent from
    state and every tool taking `Annotated[EveState, InjectedState]` fails
    pydantic validation of the injected state before its body ever runs.
    Not `operator.add`: `search_skills` already merges against the existing
    list and caps it, then returns the whole new list."""
    return new


class MemberContext(TypedDict):
    sub: str
    name: str
    role: str
    timezone: str
    permissions: list[str]
    local_time: str


class EveState(TypedDict):
    messages: Annotated[list, add_messages]
    member: MemberContext
    system_prompt: str
    # Written by `recall`, rendered into the system prompt by `eve` after
    # recall completes, and read by `extract`. Phase 3's tools loop reads it too.
    # `| None` because InjectedState's pydantic schema validates this field
    # strictly (unlike a plain TypedDict at runtime): a specialist tool invoked
    # before `recall` populates it - or in a test - must be able to pass `None`.
    memory: MemoryBundle | None
    # Specs only - see eve.skills.types.DynamicToolSpec. Materialized into
    # real callables fresh on every model call (eve.skills.materialize,
    # Task 11), never stored as one.
    dynamic_tools: Annotated[list[DynamicToolSpec], _replace_dynamic_tools]
