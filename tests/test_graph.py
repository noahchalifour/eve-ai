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


def test_the_stylist_is_bound_on_every_turn():
    from eve.graph import _static_tools

    assert "ask_stylist" in [t.name for t in _static_tools()]


def _fake_factory(_tier):
    return FakeToolCallingModel(messages=iter([AIMessage(content="Hi Noah.")]))


async def _no_recall(state, config):
    return {"memory": None}


async def _no_extract(state, config):
    return {}


async def _no_suggest(state, config):
    return {"suggestions": []}


async def test_graph_answers_and_appends_one_message(monkeypatch):
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    app = build_graph(
        model_factory=_fake_factory,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert result["messages"][-1].content == "Hi Noah."
    assert len(result["messages"]) == 2


async def test_graph_puts_member_context_into_state(monkeypatch):
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    app = build_graph(
        model_factory=_fake_factory,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
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
        model_factory=_fake_factory,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
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
        suggest_fn=_no_suggest,
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
        suggest_fn=_no_suggest,
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
        model_factory=factory,
        recall_fn=recall,
        extract_fn=extract,
        suggest_fn=_no_suggest,
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
        rules=[],
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
        suggest_fn=_no_suggest,
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
    monkeypatch.setattr("eve.graph._BASE_TOOLS", [get_widget])

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
        model_factory=factory,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
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
    monkeypatch.setattr("eve.graph._BASE_TOOLS", [fake_search_skills])
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
        model_factory=factory,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("roll a die")]}, CONFIG)

    assert called_with.get("invoked") is True
    assert result["messages"][-1].content == "You rolled a 4."
    assert result["dynamic_tools"] == [spec]


async def test_a_static_tool_works_on_a_fresh_thread(monkeypatch):
    """`dynamic_tools` needs a reducer to have a default.

    Every other tool test here either monkeypatches `_BASE_TOOLS` with
    fakes that take no `InjectedState`, or hand-builds a state dict that
    already carries `dynamic_tools` - so none of them exercise the only path
    production ever takes: a brand-new thread, invoked with nothing but
    `messages`. Without a reducer that channel is a `LastValue` with no value
    at all, the key is absent from the injected state, and pydantic rejects
    it for every real tool that asks for state. `search_skills` here is the
    real one, out of the real `_BASE_TOOLS`.
    """
    # No skills on disk -> `rank_skills` returns before it would embed
    # anything, so the tool completes locally. The point under test is the
    # injected state, not the ranking.
    monkeypatch.setattr(
        "eve.skills.search.load_skills", lambda mcp_tools, authored: []
    )
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    search_call = {
        "name": "search_skills", "args": {"query": "roll a die"},
        "id": "call-1", "type": "tool_call",
    }
    fake_model = FakeToolCallingModel(
        messages=iter(
            [
                AIMessage(content="", tool_calls=[search_call]),
                AIMessage(content="Nothing for that."),
            ]
        )
    )

    app = build_graph(
        model_factory=lambda _t: fake_model,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("go")]}, CONFIG)

    tool_message = next(m for m in result["messages"] if m.type == "tool")
    assert tool_message.status != "error", tool_message.content
    assert tool_message.content == "No matching skill or tool found."


async def test_a_raising_tool_degrades_to_an_error_message(monkeypatch):
    """A LiteLLM outage inside a specialist, an embedding failure inside
    `search_skills`, a Postgres failure inside `search_memory`: `ToolNode`'s
    default handler re-raises all of them, which ends the turn as a 500
    rather than as a sentence. The graph passes its own handler instead."""
    from langchain_core.tools import tool

    @tool
    async def explode(reason: str) -> str:
        """Fail."""
        raise RuntimeError(f"upstream is down: {reason}")

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")
    monkeypatch.setattr("eve.graph._BASE_TOOLS", [explode])

    call = {
        "name": "explode", "args": {"reason": "timeout"},
        "id": "call-1", "type": "tool_call",
    }
    fake_model = FakeToolCallingModel(
        messages=iter(
            [
                AIMessage(content="", tool_calls=[call]),
                AIMessage(content="Sorry, I couldn't reach that."),
            ]
        )
    )

    app = build_graph(
        model_factory=lambda _t: fake_model,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("do it")]}, CONFIG)

    tool_message = next(m for m in result["messages"] if m.type == "tool")
    assert tool_message.status == "error"
    assert "RuntimeError" in tool_message.content
    assert "upstream is down: timeout" in tool_message.content
    assert result["messages"][-1].content == "Sorry, I couldn't reach that."


async def test_the_tool_loop_is_bounded_when_the_model_never_answers(monkeypatch):
    """LangGraph's own recursion_limit defaults to 10007 and `.compile()`
    takes no override, so a model stuck emitting tool calls would burn
    thousands of paid calls. `eve` counts its own steps instead."""
    from langchain_core.tools import tool

    from eve.graph import _LOOP_EXHAUSTED
    from eve.settings import get_settings

    @tool
    async def noop() -> str:
        """Do nothing."""
        return "nothing happened"

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")
    monkeypatch.setattr("eve.graph._BASE_TOOLS", [noop])

    visits = []

    class NeverAnswers(FakeToolCallingModel):
        async def ainvoke(self, input, config=None, **kwargs):
            visits.append(1)
            return AIMessage(
                content="",
                tool_calls=[
                    {"name": "noop", "args": {}, "id": f"call-{len(visits)}",
                     "type": "tool_call"}
                ],
            )

    app = build_graph(
        model_factory=lambda _t: NeverAnswers(messages=iter([])),
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("loop forever")]}, CONFIG)

    limit = get_settings().max_tool_loop_iterations
    assert len(visits) == limit
    assert result["messages"][-1].content == _LOOP_EXHAUSTED


async def test_the_loop_budget_resets_on_the_next_turn(monkeypatch):
    """The bound is per turn, not per thread: Aegra checkpoints `messages`
    across turns, so a member whose previous turn exhausted the budget must
    still get tools on the next one."""
    from langchain_core.tools import tool

    from eve.graph import _LOOP_EXHAUSTED

    @tool
    async def noop() -> str:
        """Do nothing."""
        return "nothing happened"

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")
    monkeypatch.setattr("eve.graph._BASE_TOOLS", [noop])

    visits = []

    class NeverAnswers(FakeToolCallingModel):
        async def ainvoke(self, input, config=None, **kwargs):
            visits.append(1)
            return AIMessage(
                content="",
                tool_calls=[
                    {"name": "noop", "args": {}, "id": f"call-{len(visits)}",
                     "type": "tool_call"}
                ],
            )

    app = build_graph(
        model_factory=lambda _t: NeverAnswers(messages=iter([])),
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
    ).compile()
    first = await app.ainvoke({"messages": [HumanMessage("loop forever")]}, CONFIG)
    assert first["messages"][-1].content == _LOOP_EXHAUSTED

    after_first_turn = len(visits)
    await app.ainvoke(
        {**first, "messages": [*first["messages"], HumanMessage("try again")]}, CONFIG
    )

    assert len(visits) == after_first_turn * 2


def test_live_specs_drops_a_checkpointed_sandbox_spec_when_disabled(monkeypatch):
    """Pins the one behavior this task exists to guarantee: a thread
    checkpointed while EVE_SANDBOX_ENABLED was true must not keep offering a
    sandbox tool once the switch flips off, even though nothing rewrote its
    already-persisted `dynamic_tools` (design section 9). Exercises
    `_live_specs` directly, the function both `eve()` and `tools_node()` call
    to filter `state["dynamic_tools"]` before materializing."""
    from eve.graph import _live_specs
    from eve.settings import get_settings

    sandbox_spec = {
        "server_id": "sandbox", "tool_name": "amortise",
        "description": "Amortise a loan.", "schema": {"properties": {}},
    }
    other_spec = {
        "server_id": "mock-server", "tool_name": "roll_dice",
        "description": "Roll a die.", "schema": {"properties": {}},
    }
    state = {"dynamic_tools": [sandbox_spec, other_spec]}

    monkeypatch.setenv("EVE_SANDBOX_ENABLED", "false")
    get_settings.cache_clear()
    live = _live_specs(state)
    assert sandbox_spec not in live
    assert other_spec in live

    # And the switch actually does something: with it on, the same
    # checkpointed spec is offered again.
    monkeypatch.setenv("EVE_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("EVE_SANDBOX_API_KEY", "k" * 32)
    get_settings.cache_clear()
    live = _live_specs(state)
    assert sandbox_spec in live
    assert other_spec in live


def test_write_skill_is_bound_when_authoring_is_enabled(monkeypatch):
    monkeypatch.setenv("EVE_SELF_AUTHORING_ENABLED", "true")
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve import graph as graph_mod

    assert "write_skill" in {t.name for t in graph_mod._static_tools()}


def test_write_skill_is_unbound_by_default(monkeypatch):
    monkeypatch.setenv("EVE_SELF_AUTHORING_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve import graph as graph_mod

    names = {t.name for t in graph_mod._static_tools()}
    assert "write_skill" not in names
    # The Phase 3/4 toolset is untouched.
    assert {"ask_home", "ask_mail", "ask_finances", "ask_health", "search_skills",
            "search_memory"} <= names


def test_propose_tool_is_bound_when_the_sandbox_is_enabled(monkeypatch):
    monkeypatch.setenv("EVE_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("EVE_SANDBOX_API_KEY", "k" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve import graph as graph_mod

    assert "propose_tool" in {t.name for t in graph_mod._static_tools()}


def test_propose_tool_is_unbound_by_default(monkeypatch):
    monkeypatch.setenv("EVE_SANDBOX_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve import graph as graph_mod

    assert "propose_tool" not in {t.name for t in graph_mod._static_tools()}


def test_dispatch_computer_task_is_bound_when_enabled(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_ENABLED", "true")
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "k" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve import graph as graph_mod

    assert "dispatch_computer_task" in {t.name for t in graph_mod._static_tools()}


def test_dispatch_computer_task_is_unbound_by_default(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve import graph as graph_mod

    assert "dispatch_computer_task" not in {t.name for t in graph_mod._static_tools()}


async def test_ordinary_speech_still_routes_through_recall(monkeypatch):
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    called = {"recall": False}

    async def spy_recall(state, config):
        called["recall"] = True
        return {"memory": None}

    app = build_graph(
        model_factory=_fake_factory, recall_fn=spy_recall, extract_fn=_no_extract
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert called["recall"] is True
    assert result["messages"][-1].content == "Hi Noah."


async def test_the_voice_model_never_sees_a_previous_turns_persisted_frame(monkeypatch):
    """The plan's own invariant, restated: `persist_ui` writes the frame into
    `messages` for the client's benefit only - a reopened session replays
    `values.messages`, never the model's own context. From turn two on,
    that persisted AIMessage is ordinary history fed straight to
    `bound_model.ainvoke`, and unstripped, the model would have a worked
    example of the frame syntax in its own prior turn to imitate. A frame it
    composed itself would reach the client having passed through none of
    `protocol.validate_operation`, unlike a real one, which `stream.emit`
    gates."""
    from eve.ui import protocol as ui_protocol

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    operation = {"protocol": ui_protocol.PROTOCOL, "op": "delete", "surfaceId": "wx-1"}
    persisted = AIMessage(
        content=f"Lovely out there.\n{ui_protocol.frame([operation])}", id="a1"
    )

    seen = {}

    class RecordingModel(FakeToolCallingModel):
        async def ainvoke(self, input, config=None, **kwargs):
            seen["messages"] = input
            return AIMessage(content="You're welcome.")

    app = build_graph(
        model_factory=lambda _t: RecordingModel(messages=iter([])),
        recall_fn=_no_recall,
        extract_fn=_no_extract,
    ).compile()
    await app.ainvoke(
        {
            "messages": [
                HumanMessage("what's the weather?", id="h1"),
                persisted,
                HumanMessage("thanks", id="h2"),
            ]
        },
        CONFIG,
    )

    replayed = next(m for m in seen["messages"] if m.id == "a1")
    assert replayed.content == "Lovely out there."


async def test_suggestions_reach_final_state(monkeypatch):
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    async def fake_suggest(state, config):
        return {"suggestions": ["Just the kitchen"]}

    app = build_graph(
        model_factory=_fake_factory,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=fake_suggest,
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert result["suggestions"] == ["Just the kitchen"]


async def test_suggest_runs_after_extract(monkeypatch):
    """Deliberate ordering: with background extraction (the default),
    `extract` returns as soon as it registers its task, so its REFLEX call
    and the suggestion call overlap. Reversing these serialises them for no
    gain (ADR 0012, ADR 0013)."""
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")
    order = []

    async def recording_extract(state, config):
        order.append("extract")
        return {}

    async def recording_suggest(state, config):
        order.append("suggest")
        return {"suggestions": []}

    app = build_graph(
        model_factory=_fake_factory,
        recall_fn=_no_recall,
        extract_fn=recording_extract,
        suggest_fn=recording_suggest,
    ).compile()
    await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert order == ["extract", "suggest"]


async def test_suggestions_default_to_empty_on_a_fresh_thread(monkeypatch):
    """The reducer is what gives the channel a default. Without it the key is
    absent and every tool taking InjectedState fails pydantic validation
    (graph.py's own comment on `_last_write_wins`)."""
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    async def reads_state(state, config):
        assert state["suggestions"] == []
        return {"suggestions": []}

    app = build_graph(
        model_factory=_fake_factory,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=reads_state,
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert result["suggestions"] == []


async def test_the_suggestion_node_is_wired_by_default(monkeypatch):
    """The seam exists for tests and eval. The DEFAULT must be the real node,
    or the feature ships wired to nothing."""
    from eve.graph import build_graph as real_build_graph
    from eve.suggest import suggest

    graph = real_build_graph()
    assert graph.nodes["suggest"].runnable.afunc is suggest


async def test_the_default_suggest_node_never_leaks_chip_tokens_onto_messages(monkeypatch):
    """The one test that runs the REAL `suggest` node (no `suggest_fn`
    override) inside a compiled graph, end to end. Every other graph test in
    this file injects a fake suggest node, which is right for pinning
    ordering/wiring but leaves the biggest gap in this branch: nothing proves
    that, wired for real, the suggestion call's output reaches the `custom`
    frame and the `suggestions` state channel WITHOUT any of its tokens
    reaching `messages` - the channel a client renders as Eve's own reply.
    Without `TAG_NOSTREAM` actually taking effect, a chip could render as
    part of Eve's visible answer; this test is what would catch that."""
    from langgraph.constants import TAG_NOSTREAM

    import eve.suggest as suggest_mod

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    class FakeSuggestModel:
        """Mirrors tests/test_suggest.py's FakeModel: with_structured_output,
        with_config (recording tags), ainvoke."""

        def __init__(self):
            self.tags = None

        def with_structured_output(self, schema):
            return self

        def with_config(self, **kwargs):
            self.tags = kwargs.get("tags")
            return self

        async def ainvoke(self, messages):
            return suggest_mod.Suggestions(suggestions=["Just the kitchen", "All of them"])

    fake_suggest_model = FakeSuggestModel()
    monkeypatch.setattr(suggest_mod, "get_model", lambda _tier: fake_suggest_model)

    app = build_graph(
        model_factory=_fake_factory,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        # Deliberately no `suggest_fn`: the default must be the real node.
    ).compile()

    custom_frames = []
    message_texts = []
    async for mode, chunk in app.astream(
        {"messages": [HumanMessage("hello")]}, CONFIG, stream_mode=["custom", "messages"]
    ):
        if mode == "custom":
            custom_frames.append(chunk)
        elif mode == "messages":
            message, _meta = chunk
            if message.content:
                message_texts.append(message.content)

    # 1. Exactly one `suggestions` frame, carrying exactly the chips the fake
    #    model returned. Selected by key rather than asserting the whole list
    #    of frames: `tool_labels` shares this channel (and is pinned by its
    #    own tests below), so a positional assertion here would break on every
    #    future frame this graph learns to emit without saying anything about
    #    chips. The exactness that matters to THIS test - that no chip token
    #    reaches `messages` - is item 4, unchanged.
    chip_frames = [f for f in custom_frames if "suggestions" in f]
    assert chip_frames == [{"suggestions": ["Just the kitchen", "All of them"]}]

    # 2. The suggestion call was REFLEX-tier and non-streaming.
    assert fake_suggest_model.tags == [TAG_NOSTREAM]
    assert fake_suggest_model.tags == ["nostream"]

    # 3. The final state's `suggestions` channel carries the same list.
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)
    assert result["suggestions"] == ["Just the kitchen", "All of them"]

    # 4. `messages` carries ONLY Eve's own reply - no suggestion tokens.
    assert "".join(message_texts) == "Hi Noah."
    for text in message_texts:
        assert "kitchen" not in text
        assert "All of them" not in text


def _declaring(catalog_ids):
    return {
        "configurable": {
            "assistant_ui": {
                "protocol": "assistant-ui/1.0",
                "catalogVersion": "1",
                "catalogIds": catalog_ids,
            }
        }
    }


def _bound_types(config):
    from eve.graph import _static_tools

    tool = next(t for t in _static_tools(config) if t.name == "show_surface")
    return tool.args_schema["properties"]["components"]["items"]["properties"]["type"][
        "enum"
    ]


def test_show_surface_is_bound_only_for_a_declaring_client():
    from eve.graph import _static_tools

    names = {t.name for t in _static_tools({})}
    assert "show_surface" not in names

    assert "show_surface" in {
        t.name for t in _static_tools(_declaring(["card", "text"]))
    }


def test_the_bound_tool_advertises_exactly_what_the_client_declared():
    """The client already sends its catalog. Spending it only on a post-hoc
    refusal is what left the model guessing at component names up front."""
    assert _bound_types(_declaring(["card", "text"])) == ["card", "text"]


def test_an_undeclarable_type_never_reaches_the_model():
    """Intersected with the server catalog, so a client advertising a type
    this server cannot validate is never offered to the model."""
    assert _bound_types(_declaring(["card", "Checkbox"])) == ["card"]


def test_a_malformed_catalog_declaration_falls_back_to_the_full_catalog():
    """`capabilities()` only checks that `assistant_ui` is a dict. A
    non-list `catalogIds` must not raise and must not silently describe an
    empty catalog - `stream.supports` still refuses the emission."""
    from eve.ui import protocol as ui_protocol

    assert set(_bound_types(_declaring("card,text"))) == set(ui_protocol.CATALOG_IDS)


def test_a_submit_envelope_routes_through_ui_submit():
    import json

    from langchain_core.messages import HumanMessage

    from eve.graph import _route_after_context

    envelope = json.dumps(
        {
            "protocol": "assistant-ui/1.0",
            "sessionId": "s-1",
            "surfaceId": "sf-1",
            "actionId": "surface.submit",
            "state": {"reps": 8},
        }
    )
    declared = {
        "configurable": {
            "assistant_ui": {
                "protocol": "assistant-ui/1.0",
                "catalogVersion": "1",
                "catalogIds": ["card"],
            }
        }
    }
    state = {"messages": [HumanMessage(content=envelope)]}
    assert _route_after_context(state, declared) == "ui_submit"
    assert _route_after_context(state, {}) == "recall"
    assert (
        _route_after_context({"messages": [HumanMessage(content="hi")]}, declared)
        == "recall"
    )


async def test_a_command_tool_and_a_plain_tool_batch_in_one_round():
    """`search_skills` returns a Command (it updates `dynamic_tools`); a data
    tool returns a string. Issuing both in one round is what makes
    `[search_skills || ask_home] -> show_surface` two rounds instead of
    three, so the mix is worth pinning - it is library behaviour, not ours.

    Note: ToolNode must be invoked via a compiled StateGraph rather than bare
    `ToolNode([...]).ainvoke()` because the latter raises `ValueError: Missing
    required config key` in langgraph>=1.2.11. This StateGraph wrapper also
    matches real usage: ToolNode always runs inside a compiled graph in
    eve.graph.py, so testing it this way pins the actual behavior."""
    from typing import Annotated, TypedDict
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.tools import tool as make_tool
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import add_messages
    from langgraph.prebuilt import ToolNode
    from langgraph.types import Command

    class State(TypedDict):
        messages: Annotated[list, add_messages]
        dynamic_tools: list

    @make_tool
    def plain(text: str) -> str:
        """A plain tool."""
        return f"plain:{text}"

    @make_tool
    def commanding(text: str) -> Command:
        """A Command-returning tool."""
        return Command(
            update={
                "messages": [ToolMessage(f"cmd:{text}", tool_call_id="call-2")],
                "dynamic_tools": [],
            }
        )

    builder = StateGraph(State)
    tools_node = ToolNode([plain, commanding])
    builder.add_node("tools", tools_node)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile()

    message = AIMessage(
        content="",
        tool_calls=[
            {"name": "plain", "args": {"text": "a"}, "id": "call-1", "type": "tool_call"},
            {
                "name": "commanding",
                "args": {"text": "b"},
                "id": "call-2",
                "type": "tool_call",
            },
        ],
    )
    result = await graph.ainvoke({"messages": [message], "dynamic_tools": []})
    rendered = repr(result)
    assert "plain:a" in rendered
    assert "cmd:b" in rendered


def test_the_coding_tools_are_bound_when_coding_is_enabled(monkeypatch):
    monkeypatch.setenv("EVE_CODING_ENABLED", "true")
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve import graph as graph_mod

    names = {t.name for t in graph_mod._static_tools()}

    assert {"delegate_coding_task", "check_coding_session", "send_to_coding_session"} <= names
    get_settings.cache_clear()


def test_the_coding_tools_are_absent_when_coding_is_disabled(monkeypatch):
    monkeypatch.setenv("EVE_CODING_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve import graph as graph_mod

    assert "delegate_coding_task" not in {t.name for t in graph_mod._static_tools()}
    get_settings.cache_clear()


# --- the openers route (ADR 0018) -------------------------------------------

OPENERS_CONFIG = {
    "configurable": {
        "langgraph_auth_user": {"identity": "sub-noah"},
        "suggestions_only": True,
    }
}


async def test_an_openers_request_never_calls_the_voice_model(monkeypatch):
    """The whole point of the route. A client showing an empty chat wants
    chips, not an answer - and a VOICE call here would both cost money and
    append a message nobody asked Eve to say."""
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    called = []

    def factory(_tier):
        called.append("eve")
        return FakeToolCallingModel(messages=iter([AIMessage(content="Hi.")]))

    async def openers(state, config):
        return {"suggestions": ["What's on today?"]}

    app = build_graph(
        model_factory=factory,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
        openers_fn=openers,
    ).compile()
    result = await app.ainvoke({"messages": []}, OPENERS_CONFIG)

    assert called == []
    assert result["suggestions"] == ["What's on today?"]


async def test_an_openers_request_appends_no_message(monkeypatch):
    """An empty chat asking for openers must stay an empty chat. A message
    appended here would show up in the timeline, in the drawer's title
    derivation, and in the next turn's history."""
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    async def openers(state, config):
        return {"suggestions": ["Any mail?"]}

    app = build_graph(
        model_factory=_fake_factory,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
        openers_fn=openers,
    ).compile()
    result = await app.ainvoke({"messages": []}, OPENERS_CONFIG)

    assert result["messages"] == []


async def test_an_openers_request_still_runs_recall_but_not_extract_or_suggest(
    monkeypatch,
):
    """Recall is what makes an opener reflect who is asking. Extract has no
    exchange to mine, and `suggest` would overwrite the openers just emitted
    with an empty continuation list."""
    order = []

    async def recall(state, config):
        order.append("recall")
        return {"memory": None}

    async def extract(state, config):
        order.append("extract")
        return {}

    async def suggest(state, config):
        order.append("suggest")
        return {"suggestions": []}

    async def openers(state, config):
        order.append("openers")
        return {"suggestions": ["Any mail?"]}

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    app = build_graph(
        model_factory=_fake_factory,
        recall_fn=recall,
        extract_fn=extract,
        suggest_fn=suggest,
        openers_fn=openers,
    ).compile()
    result = await app.ainvoke({"messages": []}, OPENERS_CONFIG)

    assert order == ["recall", "openers"]
    assert result["suggestions"] == ["Any mail?"]


async def test_without_the_flag_a_normal_turn_is_unchanged(monkeypatch):
    """The route is opt-in. Every existing client and every graph consumer
    that never sends the flag must reach `eve` exactly as before."""
    order = []

    async def openers(state, config):
        order.append("openers")
        return {"suggestions": []}

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    app = build_graph(
        model_factory=_fake_factory,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
        openers_fn=openers,
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert order == []
    assert result["messages"][-1].content == "Hi Noah."


async def test_a_non_true_flag_falls_back_to_a_normal_turn(monkeypatch):
    """Fails CLOSED: the flag stops Eve answering, so anything but an explicit
    `True` must leave her answering."""
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    async def openers(state, config):
        raise AssertionError("openers must not be reached")

    app = build_graph(
        model_factory=_fake_factory,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
        openers_fn=openers,
    ).compile()
    config = {
        "configurable": {
            "langgraph_auth_user": {"identity": "sub-noah"},
            "suggestions_only": "yes",
        }
    }
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, config)

    assert result["messages"][-1].content == "Hi Noah."


# --- tool_labels -------------------------------------------------------


def test_every_tool_label_reads_like_an_activity():
    """The label table is product copy on the member's reading path, and the
    client drops a malformed one SILENTLY - falling back to the sentence-cased
    raw name, which looks exactly like not having shipped labels at all. So
    the house style is a test, not a comment.

    Run through the real `sanitise_tool_labels` rather than a reimplementation
    of its rules here: a table that survives this is a table the client will
    render."""
    from eve.graph import _TOOL_LABELS
    from eve.ui.stream import sanitise_tool_labels

    assert sanitise_tool_labels(_TOOL_LABELS) == _TOOL_LABELS, (
        "a label failed the client's own validation"
    )
    for name, label in _TOOL_LABELS.items():
        # The client draws its own progress affordance; a trailing ellipsis
        # doubles it. A terminal period makes a one-line status read as prose.
        assert not label.endswith(("...", "…", ".")), name
        assert label == label.strip(), name
        assert label[0].isupper(), f"{name}: sentence case"
        # Sentence case, not Title Case. Only the first word is capitalised;
        # `I` is the one exception, being a word that carries its capital
        # everywhere. No label here contains a proper noun.
        for word in label.split()[1:]:
            assert word == "I" or not word[0].isupper(), (
                f"{name}: {word!r} - sentence case, not Title Case"
            )
        # No jargon leaking into the chat, which is the whole point. Matched
        # on whole words so an honest word is not rejected for containing one
        # (`recorded` holds "record"); `_` is checked raw, since a raw tool
        # name reaching the member is the exact failure this table prevents.
        words = {word.strip(",'").lower() for word in label.split()}
        for jargon in ("tool", "tools", "invoke", "invoking", "call", "calling", "api"):
            assert jargon not in words, f"{name}: {jargon!r} is jargon"
        assert "_" not in label, f"{name}: a raw tool name leaked into the label"


def test_every_labelled_tool_is_a_real_tool(monkeypatch):
    """Keyed by the RAW tool name, so a renamed or deleted tool silently loses
    its label - and the degrade (sentence-casing) is invisible in production.
    Every switch on, so a label for a gated tool still counts as real."""
    from eve.graph import _TOOL_LABELS, _static_tools
    from eve.settings import get_settings

    for var in (
        "EVE_SELF_AUTHORING_ENABLED",
        "EVE_SANDBOX_ENABLED",
        "EVE_COMPUTER_ENABLED",
        "EVE_CODING_ENABLED",
        "EVE_ROUTINES_ENABLED",
    ):
        monkeypatch.setenv(var, "true")
    monkeypatch.setenv("EVE_SANDBOX_API_KEY", "k" * 32)
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "k" * 32)
    get_settings.cache_clear()

    bound = {tool.name for tool in _static_tools(_declaring(["text"]))}
    assert set(_TOOL_LABELS) <= bound, (
        f"labels for tools that do not exist: {set(_TOOL_LABELS) - bound}"
    )


def test_every_bound_tool_has_a_label(monkeypatch):
    """The other direction. A missing label is a working fallback, not a bug -
    but it is almost always an oversight when a tool is added, and this is the
    cheapest place to notice. Loosen this deliberately if a tool should stay
    unlabelled."""
    from eve.graph import _TOOL_LABELS, _static_tools
    from eve.settings import get_settings

    for var in (
        "EVE_SELF_AUTHORING_ENABLED",
        "EVE_SANDBOX_ENABLED",
        "EVE_COMPUTER_ENABLED",
        "EVE_CODING_ENABLED",
        "EVE_ROUTINES_ENABLED",
    ):
        monkeypatch.setenv(var, "true")
    monkeypatch.setenv("EVE_SANDBOX_API_KEY", "k" * 32)
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "k" * 32)
    get_settings.cache_clear()

    bound = {tool.name for tool in _static_tools(_declaring(["text"]))}
    assert bound - set(_TOOL_LABELS) == set()


def test_labels_cover_only_the_tools_bound_this_turn():
    """Intersected rather than sent whole: a client must never be told the
    name of something this deployment cannot call."""
    from eve.graph import _labels_for

    class _Tool:
        def __init__(self, name):
            self.name = name

    labels = _labels_for([_Tool("ask_mail"), _Tool("sandbox_amortise")])

    assert set(labels) == {"ask_mail"}
    assert labels["ask_mail"]


async def test_a_turn_emits_one_tool_labels_frame_before_any_tool_runs(monkeypatch):
    """End to end through a compiled graph, on the same `custom` channel the
    Flutter client reads. Ordering is the claim worth pinning: the handoff
    says a label may arrive before, during or after its call, and this asserts
    we take the simplest of the three - the whole map, once, before the first
    tool call of the turn can start."""
    from langchain_core.tools import tool

    from eve.graph import _TOOL_LABELS

    @tool
    async def ask_mail(request: str) -> str:
        """Ask the mail specialist."""
        return "one unread"

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")
    monkeypatch.setattr("eve.graph._BASE_TOOLS", [ask_mail])

    answers = iter(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_mail",
                        "args": {"request": "anything new?"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="One unread."),
        ]
    )

    class Answers(FakeToolCallingModel):
        async def ainvoke(self, input, config=None, **kwargs):
            return next(answers)

    app = build_graph(
        model_factory=lambda _t: Answers(messages=iter([])),
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
    ).compile()

    events = []
    async for mode, chunk in app.astream(
        {"messages": [HumanMessage("any mail?")]},
        CONFIG,
        stream_mode=["custom", "messages"],
    ):
        if mode == "custom" and "tool_labels" in chunk:
            events.append(("labels", chunk["tool_labels"]))
        elif mode == "messages":
            message, _meta = chunk
            if message.__class__.__name__ == "ToolMessage":
                events.append(("tool_result", message.name))

    kinds = [kind for kind, _ in events]
    assert kinds.count("labels") == 1, f"expected exactly one frame, got {kinds}"
    assert kinds.index("labels") < kinds.index("tool_result"), (
        "the label arrived after the tool it names had already finished"
    )
    assert events[kinds.index("labels")][1] == {
        "ask_mail": _TOOL_LABELS["ask_mail"]
    }


async def test_a_turn_with_no_tool_call_still_emits_its_labels(monkeypatch):
    """Labels describe the turn's bound tools, not one call. Emitting only
    once a call exists would put the frame after the call it names on every
    fast tool, which is the ordering this design exists to avoid depending
    on."""
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    app = build_graph(
        model_factory=_fake_factory,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
    ).compile()

    frames = [
        chunk
        async for mode, chunk in app.astream(
            {"messages": [HumanMessage("hello")]}, CONFIG, stream_mode=["custom"]
        )
        if isinstance(chunk, dict) and "tool_labels" in chunk
    ]

    assert len(frames) == 1
    assert frames[0]["tool_labels"]["ask_mail"]


async def test_the_label_frame_is_sent_once_not_once_per_tool_round(monkeypatch):
    """The loop runs up to `max_tool_loop_iterations` times and nothing about
    the labels changes between passes. Re-sending is idempotent for the
    member, so this is about stream noise, not correctness - but a frame per
    round on a six-round turn is six times the traffic for one dictionary."""
    from langchain_core.tools import tool

    from eve.settings import get_settings

    # Named for a tool that HAS a label: an unlabelled tool would produce no
    # frame at all, and the test would pass for the wrong reason.
    @tool
    async def ask_home() -> str:
        """Ask the home specialist."""
        return "nothing happened"

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")
    monkeypatch.setattr("eve.graph._BASE_TOOLS", [ask_home])

    calls = []

    class NeverAnswers(FakeToolCallingModel):
        async def ainvoke(self, input, config=None, **kwargs):
            calls.append(1)
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_home",
                        "args": {},
                        "id": f"call-{len(calls)}",
                        "type": "tool_call",
                    }
                ],
            )

    app = build_graph(
        model_factory=lambda _t: NeverAnswers(messages=iter([])),
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
    ).compile()

    frames = [
        chunk
        async for mode, chunk in app.astream(
            {"messages": [HumanMessage("loop")]}, CONFIG, stream_mode=["custom"]
        )
        if isinstance(chunk, dict) and "tool_labels" in chunk
    ]

    assert len(calls) == get_settings().max_tool_loop_iterations
    assert len(frames) == 1


async def test_a_label_failure_never_costs_the_member_an_answer(monkeypatch):
    """A label is a nicety. A writer that raises - a closed Aegra queue -
    must leave the turn intact, same posture as the `suggestions` frame."""
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    def _explode(_frame):
        raise RuntimeError("queue closed")

    monkeypatch.setattr("eve.ui.stream.get_stream_writer", lambda: _explode)

    app = build_graph(
        model_factory=_fake_factory,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=_no_suggest,
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert result["messages"][-1].content == "Hi Noah."


def test_a_disabled_tool_takes_its_label_with_it(monkeypatch):
    """The switches that gate a tool gate its label: a deployment with coding
    off must not advertise `Starting work on the code` for something it cannot
    call."""
    from eve.graph import _labels_for, _static_tools
    from eve.settings import get_settings

    monkeypatch.setenv("EVE_CODING_ENABLED", "false")
    get_settings.cache_clear()
    assert "delegate_coding_task" not in _labels_for(_static_tools())

    monkeypatch.setenv("EVE_CODING_ENABLED", "true")
    get_settings.cache_clear()
    assert "delegate_coding_task" in _labels_for(_static_tools())
