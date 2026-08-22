"""Graph state. `permissions` is resolved in Phase 1 and consumed in Phase 3;
carrying it now means the tools loop does not reshape state when it lands."""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages

from eve.memory.types import MemoryBundle


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
