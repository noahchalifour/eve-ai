import pytest

PURE = "def run(arguments):\n    return {'n': arguments['a'] + 1}\n"
IMPURE = "import os\n\ndef run(arguments):\n    return {'p': os.getcwd()}\n"

MEMBER_AUTHOR = {
    "sub": "sub-noah", "name": "Noah", "role": "adult",
    "timezone": "America/Toronto", "permissions": ["tools.author"],
    "local_time": "2026-08-27 09:00 EDT",
}
MEMBER_PLAIN = {**MEMBER_AUTHOR, "sub": "sub-kid", "permissions": []}


def _state(member):
    # InjectedState validates the whole EveState/MemberContext shape strictly
    # when a tool is invoked directly with .ainvoke (unlike a plain TypedDict
    # at runtime) - see tests/test_skills_authoring.py, test_specialists_base.py
    # and test_skills_search.py for the same full-state convention.
    return {
        "messages": [],
        "member": member,
        "system_prompt": "",
        "memory": None,
        "dynamic_tools": [],
        "suggestions": [],
    }


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setenv("EVE_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("EVE_SANDBOX_API_KEY", "k" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()


async def test_without_the_permission_it_never_interrupts(monkeypatch):
    """If you cannot approve, you cannot propose. There is no queue."""
    from eve.tools_authoring import propose as propose_mod

    def boom(payload):
        raise AssertionError("must not interrupt without tools.author")

    monkeypatch.setattr(propose_mod, "interrupt", boom)

    result = await propose_mod.propose_tool.ainvoke(
        {
            "name": "amortise", "description": "d",
            "args_schema": {"properties": {"a": {"type": "integer"}}},
            "source": PURE,
            "state": _state(MEMBER_PLAIN),
        },
        config={"configurable": {}},
    )
    assert "Permission denied" in result


async def test_a_failing_ast_check_returns_feedback_without_interrupting(monkeypatch):
    """Eve revises before a human is bothered."""
    from eve.tools_authoring import propose as propose_mod

    def boom(payload):
        raise AssertionError("must not interrupt on a failed check")

    monkeypatch.setattr(propose_mod, "interrupt", boom)

    result = await propose_mod.propose_tool.ainvoke(
        {
            "name": "amortise", "description": "d", "args_schema": {},
            "source": IMPURE,
            "state": _state(MEMBER_AUTHOR),
        },
        config={"configurable": {}},
    )
    assert "os" in result and "not allowed" in result


async def test_an_unmapped_schema_type_is_rejected(monkeypatch):
    """materialize.py maps only string/integer/number/boolean and silently
    falls back to str for anything else. Inheriting that wrong validation is
    a bug; refusing is not."""
    from eve.tools_authoring import propose as propose_mod

    monkeypatch.setattr(propose_mod, "interrupt", lambda p: {"approved": True})

    result = await propose_mod.propose_tool.ainvoke(
        {
            "name": "amortise", "description": "d",
            "args_schema": {"properties": {"rows": {"type": "array"}}},
            "source": PURE,
            "state": _state(MEMBER_AUTHOR),
        },
        config={"configurable": {}},
    )
    assert "array" in result


async def test_approval_persists_the_tool(monkeypatch):
    from eve.tools_authoring import propose as propose_mod

    stored = {}

    async def propose(**kw):
        stored.update(kw)
        return "tool-1"

    async def approve(tool_id, approver):
        stored["approved_by"] = approver
        return True

    monkeypatch.setattr(propose_mod, "store_propose", propose)
    monkeypatch.setattr(propose_mod, "store_approve", approve)
    monkeypatch.setattr(
        propose_mod, "interrupt", lambda payload: {"approved": True, "why": "fine"}
    )

    result = await propose_mod.propose_tool.ainvoke(
        {
            "name": "amortise", "description": "d",
            "args_schema": {"properties": {"a": {"type": "integer"}}},
            "source": PURE,
            "state": _state(MEMBER_AUTHOR),
        },
        config={"configurable": {"thread_id": "t1", "run_id": "r1"}},
    )
    assert stored["name"] == "amortise"
    assert stored["approved_by"] == "sub-noah"
    assert "approved" in result.lower()


async def test_rejection_records_why_and_does_not_approve(monkeypatch):
    from eve.tools_authoring import propose as propose_mod

    calls = {}

    async def propose(**kw):
        return "tool-1"

    async def approve(tool_id, approver):
        raise AssertionError("a rejected proposal must not be approved")

    async def reject(tool_id, why):
        calls["why"] = why

    monkeypatch.setattr(propose_mod, "store_propose", propose)
    monkeypatch.setattr(propose_mod, "store_approve", approve)
    monkeypatch.setattr(propose_mod, "store_reject", reject)
    monkeypatch.setattr(
        propose_mod, "interrupt",
        lambda payload: {"approved": False, "why": "reads a file"},
    )

    result = await propose_mod.propose_tool.ainvoke(
        {
            "name": "amortise", "description": "d",
            "args_schema": {"properties": {}}, "source": PURE,
            "state": _state(MEMBER_AUTHOR),
        },
        config={"configurable": {}},
    )
    assert calls["why"] == "reads a file"
    assert "not approved" in result.lower()


async def test_the_interrupt_payload_shows_the_approver_everything(monkeypatch):
    from eve.tools_authoring import propose as propose_mod

    seen = {}

    async def propose(**kw):
        return "tool-1"

    def capture(payload):
        seen.update(payload)
        return {"approved": False, "why": "no"}

    monkeypatch.setattr(propose_mod, "store_propose", propose)
    monkeypatch.setattr(propose_mod, "store_reject", lambda *a: _noop())
    monkeypatch.setattr(propose_mod, "interrupt", capture)

    await propose_mod.propose_tool.ainvoke(
        {
            "name": "amortise", "description": "amortise a loan",
            "args_schema": {"properties": {"a": {"type": "integer"}}},
            "source": "import math\n" + PURE,
            "state": _state(MEMBER_AUTHOR),
        },
        config={"configurable": {"thread_id": "t1"}},
    )
    assert seen["name"] == "amortise"
    assert seen["source"].startswith("import math")
    assert "math" in seen["imports"]
    assert seen["requested_by"] == "sub-noah"


async def _noop():
    return None


async def test_disabled_refuses_before_anything_else(monkeypatch):
    from eve.tools_authoring import propose as propose_mod
    from eve.settings import get_settings

    monkeypatch.setenv("EVE_SANDBOX_ENABLED", "false")
    get_settings.cache_clear()

    def boom(payload):
        raise AssertionError("must not interrupt when disabled")

    monkeypatch.setattr(propose_mod, "interrupt", boom)

    result = await propose_mod.propose_tool.ainvoke(
        {
            "name": "x", "description": "d", "args_schema": {}, "source": PURE,
            "state": _state(MEMBER_AUTHOR),
        },
        config={"configurable": {}},
    )
    assert result.startswith("error:")


async def test_an_interrupt_from_a_tool_is_not_swallowed_by_the_error_handler():
    """THE test that matters most. _handle_tool_error degrades every tool
    exception to a string; if a refactor makes it catch GraphBubbleUp too, the
    approval gate silently becomes an auto-approver."""
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.prebuilt import ToolNode
    from langgraph.types import interrupt

    from eve.graph import _handle_tool_error

    @tool
    def needs_approval(x: int) -> str:
        """Ask for approval."""
        decision = interrupt({"x": x})
        return f"decided: {decision}"

    class S(dict):
        pass

    builder = StateGraph(dict)
    builder.add_node(
        "tools", ToolNode([needs_approval], handle_tool_errors=_handle_tool_error)
    )
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    app = builder.compile(checkpointer=MemorySaver())

    config = {"configurable": {"thread_id": "t-interrupt"}}
    result = await app.ainvoke(
        {
            "messages": [
                AIMessage(
                    "",
                    tool_calls=[
                        {"name": "needs_approval", "args": {"x": 1}, "id": "c1"}
                    ],
                )
            ]
        },
        config,
    )
    assert "__interrupt__" in result, (
        "the interrupt was swallowed - the approval gate is not a gate"
    )
