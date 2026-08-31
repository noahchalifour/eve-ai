"""Integration coverage for the dynamic chat UI over the real HTTP boundary:
the graph, a real (locally-run) eve-tools process, and a stub Home Assistant
behind it. Only the model is faked.

Requires `docker compose -f docker-compose.test.yml up -d`? No - this tier
needs neither Postgres nor Redis, only the `eve_tools_server` fixture, which
starts eve-tools and the stub HA itself. Marked `integration` because it binds
real ports and spawns real processes.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from eve.family import Family, Member
from eve.graph import build_graph
from eve.ui import protocol
from tests.conftest import FakeToolCallingModel

pytestmark = pytest.mark.integration

NOAH = Member(
    sub="sub-noah",
    name="Noah",
    role="adult",
    timezone="America/Toronto",
    permissions=frozenset({"home.control"}),
)

CAPABLE_CONFIG = {
    "configurable": {
        "langgraph_auth_user": {"identity": "sub-noah"},
        "assistant_ui": {
            "protocol": "assistant-ui/1.0",
            "catalogVersion": "1",
            "catalogIds": [
                "weather",
                "column",
                "row",
                "card",
                "list",
                "grid",
                "divider",
                "text",
                "icon",
                "badge",
                "button",
                "segmentedSelection",
                "expandable",
            ],
        },
    }
}


async def _no_recall(state, config):
    return {"memory": None}


async def _no_extract(state, config):
    return {}


@pytest.fixture
def wired(eve_tools_server, monkeypatch):
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")
    monkeypatch.setenv("EVE_TOOLS_BASE_URL", eve_tools_server)
    monkeypatch.setenv("EVE_TOOLS_API_KEY", "test-key")
    from eve.settings import get_settings

    get_settings.cache_clear()
    return eve_tools_server


async def test_a_range_tap_round_trips_through_real_eve_tools(wired):
    """The whole inbound path with nothing faked below the graph: route the
    envelope, relay to eve-tools, read the stub HA's daily forecast, normalise
    it, validate it, and emit one patch on `custom`."""
    envelope = json.dumps(
        {
            "protocol": "assistant-ui/1.0",
            "sessionId": "session-1",
            "surfaceId": "wx-1",
            "actionId": "weather.rangeChanged",
            "value": "daily",
            "data": {},
        }
    )

    def unused_factory(_tier):  # pragma: no cover
        raise AssertionError("an action turn must not reach a model")

    app = build_graph(
        model_factory=unused_factory, recall_fn=_no_recall, extract_fn=_no_extract
    ).compile()

    frames = []
    async for _mode, chunk in app.astream(
        {
            "messages": [
                HumanMessage(
                    f"<assistant-ui-action>\n{envelope}\n</assistant-ui-action>"
                )
            ]
        },
        CAPABLE_CONFIG,
        stream_mode=["custom"],
    ):
        frames.append(chunk["assistant_ui"])

    assert len(frames) == 1
    assert protocol.validate_operation(frames[0]) is None
    patch = frames[0]["patch"]["dataPatch"]
    assert patch["selectedRange"] == "daily"
    assert len(patch["daily"]) == 7
    assert set(patch["daily"][0]) == {"label", "temperature", "condition"}
    assert patch["daily"][0]["condition"] == "Rain"


async def test_a_weather_request_streams_a_card_and_leaves_it_in_history(wired):
    """The outbound path: the model calls `show_weather`, the surface is built
    from the stub HA's real payload, streamed on `custom`, and left in the AI
    message so a reopened session still has it."""
    call = {"name": "show_weather", "args": {}, "id": "call-wx", "type": "tool_call"}

    # One shared instance, not one per call: `eve` calls `model_factory(Tier.VOICE)`
    # on every node revisit within a turn (see the comment on this in
    # test_graph.py's test_eve_calls_a_tool_and_returns_the_final_answer), and a
    # fresh iterator per revisit would replay the tool-call message forever
    # instead of advancing to the final answer. Four messages, not two: the
    # graph is compiled without a checkpointer, so the trailing `ainvoke`
    # below is a second, independent run through the same two rounds, drawing
    # on the same shared iterator.
    fake_model = FakeToolCallingModel(
        messages=iter(
            [
                AIMessage(content="", tool_calls=[call]),
                AIMessage(content="Grab a jacket."),
                AIMessage(content="", tool_calls=[call]),
                AIMessage(content="Grab a jacket."),
            ]
        )
    )

    def factory(_tier):
        return fake_model

    app = build_graph(
        model_factory=factory, recall_fn=_no_recall, extract_fn=_no_extract
    ).compile()
    inputs = {"messages": [HumanMessage("what's the weather at home?")]}

    frames = []
    async for _mode, chunk in app.astream(
        inputs, CAPABLE_CONFIG, stream_mode=["custom"]
    ):
        frames.append(chunk["assistant_ui"])

    assert len(frames) == 1
    surface = frames[0]["surface"]
    assert surface["catalogId"] == "weather"
    assert surface["data"]["location"] == "Home"
    assert surface["data"]["condition"] == "Partly cloudy"
    assert surface["data"]["temperature"] == 21
    assert surface["data"]["selectedRange"] == "hourly"
    assert len(surface["data"]["hourly"]) == 6
    # Absent on purpose: this is what makes tapping 7-day a round trip.
    assert "daily" not in surface["data"]

    result = await app.ainvoke(inputs, CAPABLE_CONFIG)
    assert "<assistant-ui>" in result["messages"][-1].content


async def test_no_card_is_emitted_at_a_client_that_declared_nothing(wired):
    """Fails closed. The tool is not even bound, so the model cannot call it,
    and nothing unreadable lands in the transcript."""
    app = build_graph(
        model_factory=lambda _tier: FakeToolCallingModel(
            messages=iter([AIMessage(content="It's mild out.")])
        ),
        recall_fn=_no_recall,
        extract_fn=_no_extract,
    ).compile()

    frames = []
    async for _mode, chunk in app.astream(
        {"messages": [HumanMessage("what's the weather?")]},
        {"configurable": {"langgraph_auth_user": {"identity": "sub-noah"}}},
        stream_mode=["custom"],
    ):
        frames.append(chunk)

    assert frames == []
