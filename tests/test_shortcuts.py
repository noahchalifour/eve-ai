"""Learned shortcuts (ENG-296): capture, the run-time guards, the prompt.

The store is faked here; tests/test_shortcuts_store.py covers its SQL
against real Postgres.
"""
from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from eve.shortcuts import allowlist, capture, tools
from eve.state import ambient_marker

MEMBER = {"sub": "sub-noah", "permissions": ["home.control", "health"]}
CONFIG = {"configurable": {"member": MEMBER}}


def _trace(*calls):
    """An inner specialist transcript: (name, args, result) per call."""
    messages = [HumanMessage("turn off the living room lights")]
    for i, (name, args, result) in enumerate(calls):
        messages.append(AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"c{i}"}]))
        messages.append(ToolMessage(result, tool_call_id=f"c{i}"))
    messages.append(AIMessage("Done."))
    return messages


OFF = {"domain": "light", "service": "turn_off", "entity_id": "light.living_room"}


# --- eligibility -------------------------------------------------------


def test_discovery_is_stripped_and_the_write_is_the_fingerprint():
    found = capture.eligible("home", _trace(
        ("list_entities", {"domain": "light"}, '[{"entity_id": "light.living_room"}]'),
        ("call_service", OFF, '{"ok": true}'),
    ))
    assert found == ("call_service", OFF)


def test_a_state_check_before_the_write_counts_as_discovery():
    found = capture.eligible("home", _trace(
        ("get_state", {"entity_id": "light.living_room"}, '{"state": "on"}'),
        ("call_service", OFF, '{"ok": true}'),
    ))
    assert found == ("call_service", OFF)


def test_a_single_read_is_eligible():
    assert capture.eligible("health", _trace(("get_sleep", {}, '{"hours": 7}'))) == ("get_sleep", {"days": 1})


@pytest.mark.parametrize("calls", [
    # two writes
    [("call_service", OFF, "ok"), ("call_service", {**OFF, "entity_id": "light.kitchen"}, "ok")],
    # an error
    [("call_service", OFF, "error: entity not found")],
    # a locks are confirm-risk
    [("call_service", {"domain": "lock", "service": "unlock", "entity_id": "lock.front"}, "ok")],
    # service data (brightness) is not a fixed call
    [("call_service", {**OFF, "service": "turn_on", "data": {"brightness": 40}}, "ok")],
    # an unknown service
    [("call_service", {**OFF, "service": "reload"}, "ok")],
    # domain/entity mismatch
    [("call_service", {**OFF, "domain": "switch"}, "ok")],
    # nothing but discovery
    [("list_entities", {}, "[]")],
    # two reads, no write
    [("get_state", {"entity_id": "light.a"}, "on"), ("get_state", {"entity_id": "light.b"}, "on")],
])
def test_ineligible_traces(calls):
    assert capture.eligible("home", _trace(*calls)) is None


def test_a_call_outside_the_allowlist_disqualifies_the_whole_trace():
    assert capture.eligible("mail", _trace(("send_email", {"to": "x"}, "sent"))) is None
    assert capture.eligible("home", _trace(("call_service", OFF, "ok"), ("mystery", {}, "ok"))) is None


def test_on_and_off_share_a_fingerprint_with_different_variants():
    on = {**OFF, "service": "turn_on"}
    a = capture.fingerprint("home", "call_service", OFF)
    b = capture.fingerprint("home", "call_service", on)
    assert a[0] == b[0]
    assert (a[1], b[1]) == ("turn_off", "turn_on")
    assert a[2] == {"domain": "light", "entity_id": "light.living_room"}


def test_corrections_are_recognised():
    for text in ("no, the kitchen ones", "Undo that", "I said bedroom", "actually leave them on"):
        assert capture.is_correction(text)
    for text in ("turn off the lights", "nothing else, thanks"):
        assert not capture.is_correction(text)


# --- observe() gating --------------------------------------------------


@pytest.fixture
def recorded(monkeypatch):
    from eve.settings import get_settings

    monkeypatch.setenv("EVE_SHORTCUTS_ENABLED", "true")
    get_settings.cache_clear()
    seen = []

    async def fake_observe(*args):
        seen.append(args)

    from eve.shortcuts import store

    monkeypatch.setattr(store, "observe", fake_observe)
    yield seen
    get_settings.cache_clear()


async def test_observe_records_a_typed_turn(recorded):
    outer = [HumanMessage("lights off")]
    capture.observe("home", _trace(("call_service", OFF, "ok")), outer, MEMBER, "t-1")
    await capture.drain()
    assert recorded == [("sub-noah", "home", "call_service", OFF, "lights off", "t-1")]


async def test_observe_ignores_ambient_and_web_turns(recorded):
    ambient = [HumanMessage(ambient_marker("Noah") + "\nA routine fired.")]
    web = [HumanMessage("lights off"),
           AIMessage("", tool_calls=[{"name": "web_search", "args": {"query": "x"}, "id": "w"}])]
    for outer in (ambient, web):
        capture.observe("home", _trace(("call_service", OFF, "ok")), outer, MEMBER, "t-1")
    await capture.drain()
    assert recorded == []


async def test_observe_does_nothing_when_disabled(monkeypatch):
    from eve.settings import get_settings

    get_settings.cache_clear()
    called = []
    monkeypatch.setattr(capture, "_spawn", lambda coro: (called.append(1), coro.close()))
    capture.observe("home", _trace(("call_service", OFF, "ok")), [HumanMessage("x")], MEMBER, "t")
    assert called == []


# --- run_shortcut ------------------------------------------------------


ROW = {
    "id": "s-1", "name": "living-room-light", "specialist": "home", "tool": "call_service",
    "fixed_args": {"domain": "light", "entity_id": "light.living_room"},
    "variant_key": "service", "variants": ["turn_off", "turn_on"], "phrasings": ["lights off"],
    "permission": "home.control",
}


@pytest.fixture
def shortcut_env(monkeypatch):
    from eve.shortcuts import store

    state = {"row": dict(ROW), "hits": 0, "failures": 0, "invoked": [], "result": '{"ok": true}'}

    async def by_name(sub, name):
        return state["row"] if name == state["row"]["name"] and sub == "sub-noah" else None

    async def hit(_id):
        state["hits"] += 1

    async def fail(_id, limit):
        state["failures"] += 1
        return state["failures"] >= limit

    async def fake_invoke(tool, arguments, timeout=15.0, **_kw):
        state["invoked"].append((tool, arguments))
        return state["result"]

    monkeypatch.setattr(store, "active_by_name", by_name)
    monkeypatch.setattr(store, "record_hit", hit)
    monkeypatch.setattr(store, "record_failure", fail)
    monkeypatch.setattr(allowlist, "invoke", fake_invoke)
    return state


async def test_run_makes_the_call_directly(shortcut_env):
    out = await tools.run_shortcut.ainvoke({"name": "living-room-light", "option": "turn_on"}, CONFIG)
    assert out == '{"ok": true}'
    assert shortcut_env["invoked"] == [("home.call_service", {
        "domain": "light", "service": "turn_on", "entity_id": "light.living_room", "data": {}})]
    assert shortcut_env["hits"] == 1


async def test_run_refuses_an_unobserved_option(shortcut_env):
    out = await tools.run_shortcut.ainvoke({"name": "living-room-light", "option": "toggle"}, CONFIG)
    assert out.startswith("error: option must be one of")
    assert shortcut_env["invoked"] == []


async def test_run_rechecks_permission_every_time(shortcut_env):
    config = {"configurable": {"member": {**MEMBER, "permissions": ["health"]}}}
    out = await tools.run_shortcut.ainvoke({"name": "living-room-light", "option": "turn_on"}, config)
    assert out.startswith("Permission denied")
    assert shortcut_env["invoked"] == []


async def test_run_rechecks_the_allowlist_on_the_stored_row(shortcut_env):
    shortcut_env["row"]["fixed_args"] = {"domain": "lock", "entity_id": "lock.front"}
    shortcut_env["row"]["variants"] = ["unlock"]
    out = await tools.run_shortcut.ainvoke({"name": "living-room-light", "option": "unlock"}, CONFIG)
    assert "ask_home" in out
    assert shortcut_env["invoked"] == []


async def test_a_failure_falls_back_to_the_specialist_and_counts(shortcut_env):
    shortcut_env["result"] = "error: entity light.living_room not found"
    out = await tools.run_shortcut.ainvoke({"name": "living-room-light", "option": "turn_off"}, CONFIG)
    assert "ask_home instead" in out
    assert shortcut_env["failures"] == 1 and shortcut_env["hits"] == 0


async def test_an_unknown_name_points_at_the_specialist(shortcut_env):
    out = await tools.run_shortcut.ainvoke({"name": "garage"}, CONFIG)
    assert "no shortcut named" in out


async def test_a_single_option_shortcut_needs_no_option(shortcut_env):
    shortcut_env["row"]["variants"] = ["turn_off"]
    out = await tools.run_shortcut.ainvoke({"name": "living-room-light"}, CONFIG)
    assert out == '{"ok": true}'


# --- prompt ------------------------------------------------------------


def test_render_lists_name_options_and_an_example():
    text = tools.render([ROW])
    assert "## Your shortcuts" in text
    assert '- living-room-light (option: turn_off | turn_on): turn_off / turn_on light.living_room - e.g. "lights off"' in text
    assert tools.render([]) == ""


def test_the_system_prompt_carries_the_shortcuts_section():
    from eve.context import build_system_prompt

    member = {"name": "Noah", "role": "adult", "local_time": "now"}
    bundle = {"profile": [], "household": [], "episodic": [], "rules": [], "digest": None,
              "vector_used": False, "latency_ms": 0.0, "shortcuts": [ROW]}
    prompt = build_system_prompt("persona", member, bundle)
    assert "## Your shortcuts" in prompt
    assert "## What you remember" not in prompt
    # A bundle checkpointed before the key existed still renders.
    del bundle["shortcuts"]
    assert "## Your shortcuts" not in build_system_prompt("persona", member, bundle)


def test_allowlist_contains_no_confirm_risk_domain():
    assert "lock" not in allowlist.SAFE_HOME_DOMAINS
    assert "cover" not in allowlist.SAFE_HOME_DOMAINS
    assert {"light", "switch", "fan", "scene", "media_player"} <= allowlist.SAFE_HOME_DOMAINS
    json.dumps(sorted(allowlist.SAFE_HOME_SERVICES))
