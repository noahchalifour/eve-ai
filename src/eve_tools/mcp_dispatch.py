"""Generic dispatcher for dynamically-discovered MCP tools. A fresh connection
per call, not a kept-open session - EveState may checkpoint across process
restarts, and this side is only ever handed a server id and tool name, never
a live session (design doc section 5.1's constraint, mirrored here for
symmetry).
"""

from __future__ import annotations

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from eve_tools.mcp_servers import server_params_for


async def invoke(server_id: str, tool_name: str, arguments: dict) -> dict:
    params = server_params_for(server_id)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            return {"content": [c.model_dump() for c in result.content]}
