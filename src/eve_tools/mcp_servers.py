"""Registered MCP servers, by id. Empty in production until a concrete skill
needs one (design doc section 2.1's non-goal) - populated in tests with a
local mock server's connection parameters.
"""

from __future__ import annotations

from mcp import StdioServerParameters

_SERVERS: dict[str, StdioServerParameters] = {}


def register(server_id: str, params: StdioServerParameters) -> None:
    _SERVERS[server_id] = params


def server_params_for(server_id: str) -> StdioServerParameters:
    if server_id not in _SERVERS:
        raise KeyError(f"no MCP server registered as {server_id!r}")
    return _SERVERS[server_id]
