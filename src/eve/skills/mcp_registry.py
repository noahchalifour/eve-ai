"""Static metadata for registered MCP servers - name, description, and
argument schema, known without opening a connection. Opening the connection
and calling the tool is eve-tools' job (eve_tools.mcp_dispatch, Task 15);
this side only needs enough to rank and describe a tool in search_skills
(Task 10). Empty in production until a concrete skill needs one (design
doc section 2.1's non-goal); populated in tests with a mock server's specs.
"""

from __future__ import annotations

from eve.skills.types import DynamicToolSpec

_REGISTERED: list[DynamicToolSpec] = []


def register(spec: DynamicToolSpec) -> None:
    _REGISTERED.append(spec)


def registered_mcp_tools() -> list[DynamicToolSpec]:
    return list(_REGISTERED)
