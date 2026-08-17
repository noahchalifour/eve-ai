from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from eve.family import Family, Member
from eve.graph import build_graph

NOAH = Member(
    sub="sub-noah",
    name="Noah",
    role="adult",
    timezone="America/Toronto",
    permissions=frozenset({"spend"}),
)
CONFIG = {"configurable": {"langgraph_auth_user": {"identity": "sub-noah"}}}


def _fake_factory(_tier):
    return GenericFakeChatModel(messages=iter([AIMessage(content="Hi Noah.")]))


async def test_graph_answers_and_appends_one_message(monkeypatch):
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    app = build_graph(model_factory=_fake_factory).compile()
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert result["messages"][-1].content == "Hi Noah."
    assert len(result["messages"]) == 2


async def test_graph_puts_member_context_into_state(monkeypatch):
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    app = build_graph(model_factory=_fake_factory).compile()
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert result["member"]["name"] == "Noah"
    assert result["member"]["permissions"] == ["spend"]
    assert "You are Eve." in result["system_prompt"]


async def test_system_prompt_is_sent_to_the_model_and_not_stored_in_messages(
    monkeypatch,
):
    seen = {}

    class RecordingModel(GenericFakeChatModel):
        async def ainvoke(self, input, config=None, **kwargs):
            seen["messages"] = input
            return AIMessage(content="ok")

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    app = build_graph(
        model_factory=lambda _t: RecordingModel(messages=iter([]))
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert seen["messages"][0].type == "system"
    assert "You are Eve." in seen["messages"][0].content
    # The system prompt is rebuilt every turn, never persisted into history.
    assert all(m.type != "system" for m in result["messages"])
