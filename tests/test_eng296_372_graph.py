"""Graph-level behaviour for ENG-296 (shortcuts) and ENG-372 (general tools):
what is bound under which switch, and a shortcut turn end to end with a fake
model."""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from eve.family import Family, Member
from eve.graph import build_graph
from tests.conftest import FakeToolCallingModel

NOAH = Member(sub="sub-noah", name="Noah", role="adult", timezone="America/Toronto",
              permissions=frozenset({"home.control"}))
CONFIG = {"configurable": {"langgraph_auth_user": {"identity": "sub-noah"}, "thread_id": "t-1"}}

ALWAYS = {"calculate", "date_time", "get_calendar", "get_weather",
          "list_add", "list_show", "list_remove", "list_clear"}


def _names(monkeypatch, **env):
    from eve.graph import _static_tools
    from eve.settings import get_settings

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    return {t.name for t in _static_tools()}


def test_general_tools_are_always_bound_and_gated_ones_are_not(monkeypatch):
    names = _names(monkeypatch)
    assert ALWAYS <= names
    assert not names & {"web_search", "fetch_url", "run_shortcut", "set_reminder"}


def test_switches_bind_their_tools(monkeypatch):
    names = _names(monkeypatch, EVE_WEB_ENABLED="true", EVE_SHORTCUTS_ENABLED="true",
                   EVE_ROUTINES_ENABLED="true")
    assert {"web_search", "fetch_url", "run_shortcut",
            "set_reminder", "list_reminders", "cancel_reminder"} <= names


ROW = {
    "id": "s-1", "name": "living-room-light", "specialist": "home", "tool": "call_service",
    "fixed_args": {"domain": "light", "entity_id": "light.living_room"},
    "variant_key": "service", "variants": ["turn_off", "turn_on"], "phrasings": ["lights off"],
    "permission": "home.control", "hits": 0,
}


async def test_a_shortcut_turn_never_reaches_the_specialist(monkeypatch):
    from eve.settings import get_settings
    from eve.shortcuts import allowlist, store

    monkeypatch.setenv("EVE_SHORTCUTS_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    async def by_name(sub, name):
        return ROW if name == ROW["name"] else None

    async def noop(*_a, **_kw):
        return False

    invoked = []

    async def fake_invoke(tool, arguments, timeout=15.0, **_kw):
        invoked.append(tool)
        return '{"ok": true}'

    monkeypatch.setattr(store, "active_by_name", by_name)
    monkeypatch.setattr(store, "record_hit", noop)
    monkeypatch.setattr(allowlist, "invoke", fake_invoke)

    def specialist_must_not_run(*_a, **_kw):
        pytest.fail("ask_home was called on a shortcut turn")

    monkeypatch.setattr("eve.specialists.base.create_agent", specialist_must_not_run)

    prompts = []

    class Model(FakeToolCallingModel):
        async def ainvoke(self, messages, config=None, **kwargs):
            prompts.append(messages[0].content)
            if len(prompts) == 1:
                return AIMessage("", tool_calls=[{
                    "name": "run_shortcut", "args": {"name": "living-room-light", "option": "turn_off"},
                    "id": "c1", "type": "tool_call"}])
            return AIMessage("Done - the living room lights are off.")

    async def recall_with_shortcut(state, config):
        return {"memory": {"profile": [], "household": [], "episodic": [], "rules": [],
                           "digest": None, "vector_used": False, "latency_ms": 0.0,
                           "shortcuts": [ROW]}}

    async def nothing(state, config):
        return {}

    async def no_chips(state, config):
        return {"suggestions": []}

    app = build_graph(model_factory=lambda _t: Model(messages=iter([])),
                      recall_fn=recall_with_shortcut, extract_fn=nothing, suggest_fn=no_chips,
                      title_fn=nothing).compile()
    result = await app.ainvoke({"messages": [HumanMessage("turn off the living room lights")]}, CONFIG)

    assert "## Your shortcuts" in prompts[0]
    assert "living-room-light" in prompts[0]
    assert invoked == ["home.call_service"]
    assert result["messages"][-1].content == "Done - the living room lights are off."


async def test_a_specialist_run_is_observed_for_promotion(monkeypatch):
    """The capture hook in build_specialist sees the inner trace and records
    the single allowlisted call it reduced to."""
    from langchain_core.messages import ToolMessage

    from eve.settings import get_settings
    from eve.shortcuts import capture, store
    from eve.specialists.base import build_specialist
    from eve.specialists.home import call_service, get_state, list_entities

    monkeypatch.setenv("EVE_SHORTCUTS_ENABLED", "true")
    get_settings.cache_clear()
    seen = []

    async def fake_observe(*args):
        seen.append(args)

    monkeypatch.setattr(store, "observe", fake_observe)

    off = {"domain": "light", "service": "turn_off", "entity_id": "light.living_room"}

    class Agent:
        async def ainvoke(self, payload, config):
            return {"messages": [
                *payload["messages"],
                AIMessage("", tool_calls=[{"name": "list_entities", "args": {"domain": "light"}, "id": "a"}]),
                ToolMessage("[...]", tool_call_id="a"),
                AIMessage("", tool_calls=[{"name": "call_service", "args": off, "id": "b"}]),
                ToolMessage('{"ok": true}', tool_call_id="b"),
                AIMessage("Turned off the living room lights."),
            ]}

    monkeypatch.setattr("eve.specialists.base.create_agent", lambda *a, **kw: Agent())
    ask = build_specialist("home", [list_entities, get_state, call_service], "x", "home.control",
                           model_factory=lambda _t: None)
    member = {"sub": "sub-noah", "name": "Noah", "role": "adult", "timezone": "UTC",
              "permissions": ["home.control"], "local_time": "now"}
    state = {"messages": [HumanMessage("lights off please")], "member": member, "system_prompt": "",
             "memory": None, "dynamic_tools": [], "suggestions": []}
    out = await ask.ainvoke({"type": "tool_call", "name": "ask_home", "id": "x",
                             "args": {"request": "turn off living room lights", "state": state}},
                            {"configurable": {"thread_id": "t-9"}})
    await capture.drain()
    assert out.content == "Turned off the living room lights."
    assert seen == [("sub-noah", "home", "call_service", off, "lights off please", "t-9")]
