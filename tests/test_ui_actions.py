"""Recognising a UI tap in what arrives as ordinary user text."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from eve.ui import actions as actions_module
from eve.ui import protocol, stream
from eve.ui.actions import parse_action

ENVELOPE = {
    "protocol": "assistant-ui/1.0",
    "sessionId": "session-1",
    "surfaceId": "wx-1",
    "actionId": "weather.rangeChanged",
    "value": "daily",
    "data": {"location": "Home", "selectedRange": "hourly"},
}


def _encoded(envelope=None) -> str:
    body = json.dumps(envelope or ENVELOPE)
    return f"<assistant-ui-action>\n{body}\n</assistant-ui-action>"


def test_a_wrapped_envelope_is_recognised():
    """The client wraps unconditionally on every provider, LangGraph
    included - `langgraph_agent_service.dart` calls `encodeAction` with no
    branch."""
    assert parse_action(_encoded()) == ENVELOPE


def test_a_bare_envelope_is_also_recognised():
    """Tolerated so a future client that drops the markers on a native
    channel still works, rather than silently falling through to the model as
    a wall of JSON."""
    assert parse_action(json.dumps(ENVELOPE)) == ENVELOPE


def test_ordinary_member_speech_is_not_an_action():
    assert parse_action("what's the weather like?") is None
    assert parse_action("") is None
    assert parse_action(None) is None
    assert parse_action([{"type": "text", "text": "hello"}]) is None


def test_an_unclosed_or_malformed_envelope_is_not_an_action():
    """Never guess. A half-arrived envelope has to become a normal Eve turn,
    not a patch built from whatever parsed."""
    assert parse_action("<assistant-ui-action>\n{\"protocol\":") is None
    assert parse_action(_encoded()[:-4]) is None
    assert parse_action("<assistant-ui-action>\nnot json\n</assistant-ui-action>") is None


def test_the_wrong_protocol_version_is_not_an_action():
    assert parse_action(_encoded({**ENVELOPE, "protocol": "assistant-ui/2.0"})) is None


def test_an_action_id_outside_the_v1_contract_is_rejected():
    """V1 has exactly one interactive contract. A crafted envelope naming a
    made-up action must not reach a handler."""
    assert parse_action(_encoded({**ENVELOPE, "actionId": "lights.toggle"})) is None


def test_an_envelope_without_a_surface_id_is_rejected():
    envelope = {key: value for key, value in ENVELOPE.items() if key != "surfaceId"}
    assert parse_action(_encoded(envelope)) is None
    assert parse_action(_encoded({**ENVELOPE, "surfaceId": ""})) is None


STATE_MEMBER = {
    "sub": "sub-noah",
    "name": "Noah",
    "role": "adult",
    "timezone": "America/Toronto",
    "permissions": [],
    "local_time": "2026-08-31 14:00 EDT",
}

PAYLOAD = {
    "entity_id": "weather.home",
    "location": "Home",
    "condition": "partlycloudy",
    "temperature": 21.4,
    "hourly": [],
    "daily": [
        {"datetime": "2026-09-05T12:00:00+00:00", "condition": "rainy", "temperature": 17}
    ],
}


def _state(text: str) -> dict:
    return {
        "messages": [HumanMessage(content=text, id="h1")],
        "member": STATE_MEMBER,
        "system_prompt": "",
        "memory": None,
        "dynamic_tools": [],
    }


@pytest.fixture
def written(monkeypatch):
    frames: list = []
    monkeypatch.setattr(stream, "get_stream_writer", lambda: frames.append)
    return frames


def _serve(monkeypatch, payload):
    async def fake_invoke(tool, arguments, **kwargs):
        assert tool == "home.weather"
        return json.dumps(payload) if isinstance(payload, dict) else payload

    monkeypatch.setattr(actions_module, "invoke", fake_invoke)


async def test_a_range_tap_emits_one_patch_for_that_surface(monkeypatch, written):
    _serve(monkeypatch, PAYLOAD)

    await actions_module.ui_action(_state(_encoded()), {})

    assert len(written) == 1
    operation = written[0]["assistant_ui"]
    assert protocol.validate_operation(operation) is None
    assert operation["op"] == "patch"
    assert operation["surfaceId"] == "wx-1"
    assert operation["patch"]["dataPatch"]["selectedRange"] == "daily"


async def test_the_forecast_is_refetched_not_taken_from_the_envelope(monkeypatch, written):
    """The envelope's `data` arrives from the client. Trusting it would let a
    crafted envelope choose what the card says."""
    _serve(monkeypatch, PAYLOAD)
    envelope = {**ENVELOPE, "data": {"daily": [{"label": "X", "temperature": 99, "condition": "Nope"}]}}

    await actions_module.ui_action(_state(_encoded(envelope)), {})

    cells = written[0]["assistant_ui"]["patch"]["dataPatch"]["daily"]
    assert cells[0]["label"] == "Sat"
    assert cells[0]["temperature"] == 17


async def test_the_turn_leaves_a_readable_transcript_behind(monkeypatch, written):
    """Two things at once. The raw envelope is replaced in place (same message
    id, so `add_messages` overwrites rather than appends) so a reopened session
    does not show a user bubble full of JSON. And the patch is written into an
    AI message as a portable frame, because `custom` frames are streamed and
    never stored - `loadHistory` replays `values.messages` and nothing else."""
    _serve(monkeypatch, PAYLOAD)

    result = await actions_module.ui_action(_state(_encoded()), {})

    human, ai = result["messages"]
    assert isinstance(human, HumanMessage)
    assert human.id == "h1"
    assert human.content == "Show the 7-day forecast."
    assert isinstance(ai, AIMessage)
    assert ai.content.startswith("<assistant-ui>\n")
    assert ai.content.endswith("\n</assistant-ui>")
    assert json.loads(ai.content.splitlines()[1])["op"] == "patch"


async def test_an_unsupported_range_raises(monkeypatch, written):
    _serve(monkeypatch, PAYLOAD)

    with pytest.raises(actions_module.UiActionError):
        await actions_module.ui_action(_state(_encoded({**ENVELOPE, "value": "yearly"})), {})

    assert written == []


async def test_a_failed_forecast_raises_so_the_client_can_offer_a_retry(
    monkeypatch, written
):
    """The one place in Eve where a failing external system is not swallowed
    into a returned string: there is no model in this branch, and the
    protocol's failure contract IS an error event - the surface keeps its last
    valid data, goes to `error`, and offers a retry. Returning quietly would
    leave the card spinning on "Loading forecast"."""
    _serve(monkeypatch, "error: eve-tools unavailable (ConnectError)")

    with pytest.raises(actions_module.UiActionError):
        await actions_module.ui_action(_state(_encoded()), {})

    assert written == []


async def test_a_range_the_home_publishes_nothing_for_raises(monkeypatch, written):
    _serve(monkeypatch, {**PAYLOAD, "daily": []})

    with pytest.raises(actions_module.UiActionError):
        await actions_module.ui_action(_state(_encoded()), {})

    assert written == []
