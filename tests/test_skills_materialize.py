"""tests/test_skills_materialize.py"""
from unittest.mock import AsyncMock

from eve.skills.materialize import materialize
from eve.skills.types import DynamicToolSpec


async def test_materialized_tool_calls_the_mcp_dispatcher(monkeypatch):
    mock_invoke = AsyncMock(return_value='{"total": 4}')
    monkeypatch.setattr("eve.skills.materialize.invoke", mock_invoke)
    spec: DynamicToolSpec = {
        "server_id": "mock-server",
        "tool_name": "roll_dice",
        "description": "Roll a die with the given number of sides.",
        "schema": {"properties": {"sides": {"type": "integer"}}},
    }
    tool_obj = materialize(spec)
    assert tool_obj.name == "mock-server_roll_dice"
    result = await tool_obj.ainvoke({"sides": 6})
    assert result == '{"total": 4}'
    mock_invoke.assert_awaited_once_with(
        "mcp.invoke",
        {"server_id": "mock-server", "tool_name": "roll_dice", "arguments": {"sides": 6}},
    )
