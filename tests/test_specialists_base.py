from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from eve.specialists.base import build_specialist
from eve.state import EveState
from tests.conftest import FakeToolCallingModel

MEMBER = {
    "sub": "sub-noah",
    "name": "Noah",
    "role": "adult",
    "timezone": "America/Vancouver",
    "permissions": ["home.control"],
    "local_time": "2026-08-21 09:00 PDT",
}
STATE: EveState = {
    "messages": [],
    "member": MEMBER,
    "system_prompt": "",
    "memory": None,
    "dynamic_tools": [],
}
CONFIG = {"configurable": {}}


@tool
async def get_widget(name: str) -> str:
    """Look up a widget."""
    return f"widget:{name}"


def _factory_with(*ai_messages):
    return lambda _tier: FakeToolCallingModel(messages=iter(ai_messages))


async def test_denies_the_call_before_touching_the_model():
    calls = []

    def factory(_tier):
        calls.append(1)
        return FakeToolCallingModel(messages=iter([AIMessage("should not run")]))

    ask = build_specialist(
        name="widgets",
        tools=[get_widget],
        system_prompt="You manage widgets.",
        permission="widgets.manage",
        model_factory=factory,
    )
    result = await ask.ainvoke(
        {"request": "get the sprocket", "state": STATE, "config": CONFIG}
    )
    assert "Permission denied" in result
    assert "widgets.manage" in result
    assert calls == [], "the model must never be called on a denied request"


async def test_runs_the_inner_tool_loop_and_returns_the_final_answer():
    tool_call = {
        "name": "get_widget",
        "args": {"name": "sprocket"},
        "id": "call-1",
        "type": "tool_call",
    }
    ask = build_specialist(
        name="home",
        tools=[get_widget],
        system_prompt="You manage widgets.",
        permission="home.control",
        model_factory=_factory_with(
            AIMessage(content="", tool_calls=[tool_call]),
            AIMessage(content="Found it: widget:sprocket"),
        ),
    )
    result = await ask.ainvoke(
        {"request": "look up the sprocket", "state": STATE, "config": CONFIG}
    )
    assert result == "Found it: widget:sprocket"


async def test_system_prompt_is_sent_as_a_developer_message_not_a_system_message(
    monkeypatch,
):
    """The ChatGPT backend rejects plain system messages outright - live-
    verified against the real proxy (not by this or any other test in this
    file, which all fake the model and never inspect what create_agent
    receives): every real specialist call 400s with "System messages are
    not allowed" without this. graph.py's Eve-level persona message already
    carries this marker; this is the same fix for every specialist."""
    seen = {}

    class _StubAgent:
        async def ainvoke(self, _input, _config):
            return {"messages": [AIMessage("ok")]}

    def fake_create_agent(model, tools, *, system_prompt=None, **kwargs):
        seen["system_prompt"] = system_prompt
        return _StubAgent()

    ask = build_specialist(
        name="widgets",
        tools=[],
        system_prompt="You manage widgets.",
        permission="widgets.manage",
        model_factory=_factory_with(AIMessage("ok")),
    )
    monkeypatch.setattr("eve.specialists.base.create_agent", fake_create_agent)
    state = {**STATE, "member": {**MEMBER, "permissions": ["widgets.manage"]}}
    await ask.ainvoke({"request": "go", "state": state, "config": CONFIG})

    prompt = seen["system_prompt"]
    assert isinstance(prompt, SystemMessage)
    assert prompt.additional_kwargs.get("__openai_role__") == "developer"
    assert prompt.content == "You manage widgets."


async def test_allows_any_of_a_permission_list():
    ask = build_specialist(
        name="mail",
        tools=[],
        system_prompt="You manage mail.",
        permission=["mail.read", "mail.send"],
        model_factory=_factory_with(AIMessage("ok")),
    )
    member_with_read_only = {**MEMBER, "permissions": ["mail.read"]}
    state = {**STATE, "member": member_with_read_only}
    result = await ask.ainvoke({"request": "summarise my inbox", "state": state, "config": CONFIG})
    assert result == "ok"
