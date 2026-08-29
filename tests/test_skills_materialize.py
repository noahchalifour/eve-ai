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


async def test_a_sandbox_spec_dispatches_to_the_sandbox(monkeypatch):
    from eve.skills import materialize as materialize_mod

    seen = {}

    async def invoke(tool, arguments, timeout=15.0, *, target="tools", extra=None):
        seen["target"] = target
        seen["extra"] = extra
        return '{"n": 42}'

    monkeypatch.setattr(materialize_mod, "invoke", invoke)

    spec = {
        "server_id": "sandbox",
        "tool_name": "amortise",
        "description": "d",
        "schema": {"properties": {"a": {"type": "integer"}}},
        "source": "def run(arguments):\n    return {}\n",
        "source_sha256": "a" * 64,
    }
    built = materialize_mod.materialize(spec)
    await built.ainvoke({"a": 1})

    assert seen["target"] == "sandbox"
    assert seen["extra"]["source_sha256"] == "a" * 64


async def test_a_sandbox_call_counts_the_invocation(monkeypatch):
    """A tool approved and then used once was a wasted approval. That is only
    visible in `eve-tool list` if dispatch records the use."""
    import sys
    import types as pytypes

    from eve.skills import materialize as materialize_mod

    counted = []

    async def invoke(tool, arguments, timeout=15.0, *, target="tools", extra=None):
        return "{}"

    async def record_invocation(tool_id):
        counted.append(tool_id)

    fake = pytypes.ModuleType("eve.tools_authoring.store")
    fake.record_invocation = record_invocation
    monkeypatch.setitem(sys.modules, "eve.tools_authoring.store", fake)
    monkeypatch.setattr(materialize_mod, "invoke", invoke)

    built = materialize_mod.materialize(
        {
            "server_id": "sandbox", "tool_name": "amortise", "description": "d",
            "schema": {"properties": {}}, "source": "x",
            "source_sha256": "a" * 64, "tool_id": "tool-1",
        }
    )
    await built.ainvoke({})

    assert counted == ["tool-1"]


async def test_a_counting_failure_does_not_fail_the_call(monkeypatch):
    """The result is already computed. Losing a counter must not lose it."""
    import sys
    import types as pytypes

    from eve.skills import materialize as materialize_mod

    async def invoke(tool, arguments, timeout=15.0, *, target="tools", extra=None):
        return '{"n": 42}'

    async def record_invocation(tool_id):
        raise RuntimeError("postgres is down")

    fake = pytypes.ModuleType("eve.tools_authoring.store")
    fake.record_invocation = record_invocation
    monkeypatch.setitem(sys.modules, "eve.tools_authoring.store", fake)
    monkeypatch.setattr(materialize_mod, "invoke", invoke)

    built = materialize_mod.materialize(
        {
            "server_id": "sandbox", "tool_name": "amortise", "description": "d",
            "schema": {"properties": {}}, "source": "x",
            "source_sha256": "a" * 64, "tool_id": "tool-1",
        }
    )
    assert "42" in await built.ainvoke({})
