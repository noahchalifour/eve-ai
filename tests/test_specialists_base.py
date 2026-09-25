from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from eve.specialists.base import build_specialist
from eve.settings import get_settings
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
    "suggestions": [],
}
CONFIG = {"configurable": {}}


class _AgentStub:
    async def ainvoke(self, _payload, _config):
        return {"messages": [AIMessage(content="done")]}


_AGENT_STUB = _AgentStub()


@tool
async def get_widget(name: str) -> str:
    """Look up a widget."""
    return f"widget:{name}"


def _factory_with(*ai_messages):
    return lambda _tier: FakeToolCallingModel(messages=iter(ai_messages))


async def test_every_specialist_gets_a_scoped_skills_search(monkeypatch):
    captured = {}

    def _fake_create_agent(model, tools, system_prompt):
        captured["tools"] = tools
        return _AGENT_STUB

    monkeypatch.setattr("eve.specialists.base.create_agent", _fake_create_agent)

    specialist = build_specialist(
        name="widgets",
        tools=[get_widget],
        system_prompt="You handle widgets.",
        permission="home.control",
        model_factory=_factory_with(AIMessage(content="done")),
    )
    await specialist.ainvoke({"request": "hello", "state": STATE}, config=CONFIG)

    assert [t.name for t in captured["tools"]] == ["get_widget", "search_skills"]


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


def _tool_calling_model(rounds: int):
    """A model that emits `rounds` tool calls before answering."""
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "get_widget",
                    "args": {"name": f"w{i}"},
                    "id": f"call-{i}",
                    "type": "tool_call",
                }
            ],
        )
        for i in range(rounds)
    ]
    messages.append(AIMessage(content="Done."))
    return FakeToolCallingModel(messages=iter(messages))


def _widget_specialist(rounds: int):
    return build_specialist(
        name="home",
        tools=[get_widget],
        system_prompt="You manage widgets.",
        permission="home.control",
        model_factory=lambda _tier: _tool_calling_model(rounds),
    )


async def test_a_specialist_gets_its_full_iteration_budget_of_tool_rounds():
    """EVE-15: `specialist_max_iterations` is a count of model+tool rounds,
    but LangGraph's `recursion_limit` counts SUPERSTEPS, and create_agent
    spends two per round (`model`, then `tools`). Passing the setting through
    raw bought 2 rounds, not 6, so any specialist request needing a third
    tool call - search mail then open it, list accounts then pull
    transactions - died with GraphRecursionError."""
    budget = get_settings().specialist_max_iterations
    result = await _widget_specialist(budget).ainvoke(
        {"request": "look up every widget", "state": STATE, "config": CONFIG}
    )
    assert result == "Done."


async def test_exhausting_the_inner_loop_answers_a_sentence_not_a_traceback():
    """The outer loop has `_LOOP_EXHAUSTED` for this; the inner loop leaked
    `error: GraphRecursionError: Recursion limit of 6 reached...` into the
    conversation as a tool message, which Eve then relayed to the member
    verbatim (EVE-15). Whatever the budget is, blowing it has to read as
    English."""
    over = get_settings().specialist_max_iterations + 1
    result = await _widget_specialist(over).ainvoke(
        {"request": "look up every widget", "state": STATE, "config": CONFIG}
    )
    assert "GraphRecursionError" not in result
    assert "recursion" not in result.lower()
    assert "home" in result


import json

SNAPSHOT = {
    "description": "Ask the widgets specialist to handle a request in its domain.",
    "properties": {"request": {"title": "Request", "type": "string"}},
    "required": ["request"],
    "title": "ask_widgets",
    "type": "object",
}


def test_the_default_schema_is_unchanged():
    specialist = build_specialist(
        name="widgets", tools=[get_widget], system_prompt="x",
        permission="home.control", model_factory=lambda _t: None,
    )
    assert json.loads(json.dumps(specialist.tool_call_schema.model_json_schema())) == SNAPSHOT


def test_accepts_images_adds_an_optional_image_ids_list():
    specialist = build_specialist(
        name="widgets", tools=[get_widget], system_prompt="x",
        permission="home.control", model_factory=lambda _t: None, accepts_images=True,
    )
    schema = specialist.tool_call_schema.model_json_schema()
    assert "image_ids" in schema["properties"]
    assert schema["required"] == ["request"]


async def test_image_ids_become_references_on_the_inner_request(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from eve.images.store import ImageRow

    full = "aaaaaaaa-0000-4000-8000-000000000001"
    captured = {}

    class _Agent:
        async def ainvoke(self, payload, config):
            captured["payload"] = payload
            captured["config"] = config
            return {"messages": [AIMessage(content="done")]}

    def fake_create_agent(model, tools, system_prompt, middleware=()):
        captured["middleware"] = middleware
        return _Agent()

    async def fake_resolve(ref, member_sub, thread_id, *, now=None):
        now = datetime.now(UTC)
        if ref == "aaaaaaaa":
            return ImageRow(full, member_sub, thread_id, "upload", None, "image/jpeg",
                            b"", 1, 1, None, now, now + timedelta(days=1))
        return None

    monkeypatch.setattr("eve.specialists.base.create_agent", fake_create_agent)
    monkeypatch.setattr("eve.specialists.base.image_store.resolve", fake_resolve)
    specialist = build_specialist(
        name="widgets", tools=[get_widget], system_prompt="x",
        permission="home.control", model_factory=lambda _t: None, accepts_images=True,
    )
    config = {"configurable": {"thread_id": "t1"}}
    await specialist.coroutine("does this match?", STATE, config, image_ids=["aaaaaaaa", "zzzzzzzz"])

    content = captured["payload"]["messages"][0].content
    assert content == [
        {"type": "text", "text": "does this match?"},
        {"type": "eve_image", "image_id": full, "alt": "image aaaaaaaa"},
    ]
    assert len(captured["middleware"]) == 1


async def test_the_default_specialist_gets_no_middleware(monkeypatch):
    captured = {}

    def fake_create_agent(model, tools, system_prompt, middleware=()):
        captured["middleware"] = middleware
        return _AGENT_STUB

    monkeypatch.setattr("eve.specialists.base.create_agent", fake_create_agent)
    specialist = build_specialist(
        name="widgets", tools=[get_widget], system_prompt="x",
        permission="home.control", model_factory=lambda _t: None,
    )
    await specialist.coroutine("hi", STATE, CONFIG)
    assert tuple(captured["middleware"]) == ()


async def test_the_image_middleware_hydrates_the_inner_model_call(monkeypatch):
    from eve.specialists import base

    seen = {}

    async def fake_hydrate(messages, member_sub, *, native, window=None):
        seen.update(member_sub=member_sub, native=native)
        return ["hydrated"]

    class _Request:
        messages = ["raw"]

        def override(self, **kw):
            seen["override"] = kw
            return self

    async def handler(request):
        return "response"

    monkeypatch.setattr(base, "hydrate", fake_hydrate)
    monkeypatch.setattr(base, "get_config", lambda: {"configurable": {"member": {"sub": "sub-noah"}}})
    result = await base._image_middleware.awrap_model_call(_Request(), handler)

    assert result == "response"
    assert seen["override"] == {"messages": ["hydrated"]}
    assert seen["member_sub"] == "sub-noah"
