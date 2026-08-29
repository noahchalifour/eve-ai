"""Shapes only. `DynamicToolSpec` is a materializable reference to an MCP
tool, never a live callable - see design doc section 5.1: Aegra checkpoints
EveState to Postgres across every turn in a thread, and a value closing over
a live connection would either fail to serialize or silently break on the
next turn's rehydration.
"""

from __future__ import annotations

from typing import TypedDict


class DynamicToolSpec(TypedDict, total=False):
    server_id: str
    tool_name: str
    description: str
    schema: dict  # JSON schema for the tool's arguments
    # Phase 5c, sandbox specs only. The source travels in state and in the
    # request so eve-sandbox needs no database credential.
    source: str
    source_sha256: str
    tool_id: str


class SkillMatch(TypedDict):
    kind: str  # "procedure" | "mcp_tool"
    name: str
    content: str  # procedure text, or a description for an mcp_tool match
    spec: DynamicToolSpec | None
