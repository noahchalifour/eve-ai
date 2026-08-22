from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage

from eve.family import Family, Member
from eve.graph import build_graph
from tests.conftest import FakeToolCallingModel

NOAH = Member(
    sub="sub-noah",
    name="Noah",
    role="adult",
    timezone="America/Toronto",
    permissions=frozenset({"spend"}),
)
CONFIG = {"configurable": {"langgraph_auth_user": {"identity": "sub-noah"}}}


def _fake_factory(_tier):
    return FakeToolCallingModel(messages=iter([AIMessage(content="Hi Noah.")]))


async def _no_recall(state, config):
    return {"memory": None}


async def _no_extract(state, config):
    return {}


async def test_graph_answers_and_appends_one_message(monkeypatch):
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    app = build_graph(
        model_factory=_fake_factory, recall_fn=_no_recall, extract_fn=_no_extract
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert result["messages"][-1].content == "Hi Noah."
    assert len(result["messages"]) == 2


async def test_graph_puts_member_context_into_state(monkeypatch):
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    app = build_graph(
        model_factory=_fake_factory, recall_fn=_no_recall, extract_fn=_no_extract
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert result["member"]["name"] == "Noah"
    assert result["member"]["permissions"] == ["spend"]
    assert "You are Eve." in result["system_prompt"]


async def test_the_graph_streams_tokens_rather_than_one_blob(monkeypatch):
    """Eve's headline product property (ADR 0002, spec 4.2 item 3): tokens
    arrive incrementally rather than as one blob. `stream_mode="messages"` is
    the mode Aegra relays to SSE, and `await model.ainvoke` in the `eve` node
    only yields token-level chunks through it because langchain-core's
    `_should_stream` routes the call through `_astream`. Nothing else on this
    branch fails if that stops being true - the live streaming test exercises
    `model.astream` directly, a different call path.

    The other half of the mechanism, `streaming=True` reaching the real
    client, is pinned by `test_voice_model_declares_streaming` in
    tests/test_models.py; a fake model cannot carry that kwarg."""
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    app = build_graph(
        model_factory=_fake_factory, recall_fn=_no_recall, extract_fn=_no_extract
    ).compile()
    chunks = [
        chunk
        async for chunk in app.astream(
            {"messages": [HumanMessage("hello")]}, CONFIG, stream_mode="messages"
        )
    ]

    assert len(chunks) > 1, "the turn arrived as one blob, not a token stream"
    assert "".join(message.content for message, _meta in chunks) == "Hi Noah."


async def test_system_prompt_is_sent_to_the_model_and_not_stored_in_messages(
    monkeypatch,
):
    seen = {}

    class RecordingModel(FakeToolCallingModel):
        async def ainvoke(self, input, config=None, **kwargs):
            seen["messages"] = input
            return AIMessage(content="ok")

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    app = build_graph(
        model_factory=lambda _t: RecordingModel(messages=iter([])),
        recall_fn=_no_recall,
        extract_fn=_no_extract,
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert seen["messages"][0].type == "system"
    assert "You are Eve." in seen["messages"][0].content
    # The system prompt is rebuilt every turn, never persisted into history.
    assert all(m.type != "system" for m in result["messages"])


async def test_persona_is_sent_as_a_developer_message_not_a_system_message(
    monkeypatch,
):
    """The ChatGPT backend rejects system messages outright.

    Verified live on 2026-08-18: it answers `System messages are not allowed`
    and the entire turn errors, so Eve cannot speak at all. The Responses API
    wants the `developer` role instead, which langchain-openai emits from this
    marker. Without it Eve is mute against every chatgpt/* model.
    """
    seen = {}

    class RecordingModel(FakeToolCallingModel):
        async def ainvoke(self, input, config=None, **kwargs):
            seen["messages"] = input
            return AIMessage(content="ok")

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    app = build_graph(
        model_factory=lambda _t: RecordingModel(messages=iter([])),
        recall_fn=_no_recall,
        extract_fn=_no_extract,
    ).compile()
    await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    persona = seen["messages"][0]
    assert persona.additional_kwargs.get("__openai_role__") == "developer"


async def test_the_graph_runs_recall_before_eve_and_extract_after(monkeypatch):
    """Recall must inform the answer it precedes; extract must not delay it."""
    order = []

    async def recall(state, config):
        order.append("recall")
        return {"memory": None}

    async def extract(state, config):
        order.append("extract")
        return {}

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    def factory(_tier):
        order.append("eve")
        return FakeToolCallingModel(messages=iter([AIMessage(content="Hi.")]))

    app = build_graph(
        model_factory=factory, recall_fn=recall, extract_fn=extract
    ).compile()
    await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert order == ["recall", "eve", "extract"]


async def test_memory_reaches_the_system_prompt(monkeypatch):
    from datetime import UTC, datetime

    from eve.memory.types import Memory, MemoryBundle

    now = datetime.now(UTC)
    bundle = MemoryBundle(
        profile=[
            Memory(
                id="p1",
                layer="profile",
                scope_kind="member",
                scope_id="sub-noah",
                kind="fact",
                subject=None,
                content="Noah is vegetarian",
                confidence=0.7,
                salience=0.5,
                created_at=now,
                last_seen_at=now,
            )
        ],
        household=[],
        episodic=[],
        digest=None,
        vector_used=False,
        latency_ms=1.0,
    )

    async def recall(state, config):
        return {"memory": bundle}

    seen = {}

    class RecordingModel(FakeToolCallingModel):
        async def ainvoke(self, input, config=None, **kwargs):
            seen["messages"] = input
            return AIMessage(content="ok")

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    app = build_graph(
        model_factory=lambda _t: RecordingModel(messages=iter([])),
        recall_fn=recall,
        extract_fn=_no_extract,
    ).compile()
    await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert "Noah is vegetarian" in seen["messages"][0].content


async def test_eve_calls_a_tool_and_returns_the_final_answer(monkeypatch):
    from langchain_core.tools import tool

    @tool
    async def get_widget(name: str) -> str:
        """Look up a widget."""
        return f"widget:{name}"

    tool_call = {
        "name": "get_widget", "args": {"name": "sprocket"}, "id": "call-1", "type": "tool_call",
    }
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")
    monkeypatch.setattr("eve.graph._STATIC_TOOLS", [get_widget])

    # `eve` calls `model_factory(Tier.VOICE)` on every node visit, including
    # revisits within one turn's tool loop - fine in production since
    # `get_model` is `lru_cache`d (tests/conftest.py), but a plain factory
    # here would hand back a freshly-reset iterator each revisit and the
    # tool-call message would repeat forever. One shared instance mirrors
    # the real caching.
    fake_model = FakeToolCallingModel(
        messages=iter(
            [
                AIMessage(content="", tool_calls=[tool_call]),
                AIMessage(content="It's a sprocket."),
            ]
        )
    )

    def factory(_tier):
        return fake_model

    app = build_graph(
        model_factory=factory, recall_fn=_no_recall, extract_fn=_no_extract
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("what's the widget?")]}, CONFIG)

    assert result["messages"][-1].content == "It's a sprocket."
    tool_message = result["messages"][-2]
    assert tool_message.type == "tool"
    assert tool_message.content == "widget:sprocket"


async def test_a_dynamically_bound_tool_is_callable_the_turn_it_is_discovered(monkeypatch):
    from typing import Annotated

    from langchain_core.messages import ToolMessage
    from langchain_core.tools import InjectedToolCallId, tool
    from langgraph.types import Command

    spec = {
        "server_id": "mock-server", "tool_name": "roll_dice",
        "description": "Roll a die.", "schema": {"properties": {}},
    }

    @tool
    async def fake_search_skills(
        query: str, tool_call_id: Annotated[str, InjectedToolCallId]
    ) -> Command:
        """stand-in for eve.skills.search.search_skills"""
        return Command(
            update={
                "messages": [ToolMessage("Tool available: roll_dice", tool_call_id=tool_call_id)],
                "dynamic_tools": [spec],
            }
        )

    called_with = {}

    def fake_materialize(spec_):
        @tool
        async def roll_dice() -> str:
            """Roll a die."""
            called_with["invoked"] = True
            return "4"

        return roll_dice

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")
    monkeypatch.setattr("eve.graph._STATIC_TOOLS", [fake_search_skills])
    monkeypatch.setattr("eve.graph.materialize", fake_materialize)

    search_call = {
        "name": "fake_search_skills", "args": {"query": "roll a die"},
        "id": "call-1", "type": "tool_call",
    }
    dice_call = {"name": "roll_dice", "args": {}, "id": "call-2", "type": "tool_call"}

    # See the comment in test_eve_calls_a_tool_and_returns_the_final_answer:
    # one shared instance across revisits, mirroring `get_model`'s caching.
    fake_model = FakeToolCallingModel(
        messages=iter(
            [
                AIMessage(content="", tool_calls=[search_call]),
                AIMessage(content="", tool_calls=[dice_call]),
                AIMessage(content="You rolled a 4."),
            ]
        )
    )

    def factory(_tier):
        return fake_model

    app = build_graph(
        model_factory=factory, recall_fn=_no_recall, extract_fn=_no_extract
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("roll a die")]}, CONFIG)

    assert called_with.get("invoked") is True
    assert result["messages"][-1].content == "You rolled a 4."
    assert result["dynamic_tools"] == [spec]
