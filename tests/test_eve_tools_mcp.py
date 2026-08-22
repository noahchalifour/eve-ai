"""Exercises the generic MCP dispatcher against a real local MCP server run
over stdio - the "local mock server" the design doc calls for (section 2.1's
non-goal: no live MCP server ships this phase, but the plumbing is real).
"""
import sys

import pytest

from eve_tools import mcp_dispatch, mcp_servers

pytestmark = pytest.mark.integration

MOCK_SERVER_SCRIPT = """
from mcp.server.mcpserver import MCPServer

server = MCPServer("mock-server")


@server.tool()
def roll_dice() -> str:
    \"\"\"Roll a die.\"\"\"
    return "4"


server.run()
"""


@pytest.fixture
def mock_server_script(tmp_path):
    script = tmp_path / "mock_mcp_server.py"
    script.write_text(MOCK_SERVER_SCRIPT)
    return script


async def test_dispatch_invokes_a_real_local_mcp_server(mock_server_script):
    from mcp import StdioServerParameters

    mcp_servers.register(
        "mock-server", StdioServerParameters(command=sys.executable, args=[str(mock_server_script)])
    )
    result = await mcp_dispatch.invoke("mock-server", "roll_dice", {})
    assert result["content"][0]["text"] == "4"


async def test_dispatch_raises_for_an_unregistered_server():
    with pytest.raises(KeyError):
        await mcp_dispatch.invoke("nonexistent-server", "anything", {})
