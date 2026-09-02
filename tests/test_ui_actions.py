"""Recognising a UI tap in what arrives as ordinary user text."""

from __future__ import annotations

import json

from eve.ui.actions import parse_action

ENVELOPE = {
    "protocol": "assistant-ui/1.0",
    "sessionId": "session-1",
    "surfaceId": "sf-1",
    "actionId": "surface.submit",
    "value": None,
    "data": {},
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
    """Only surface.submit is in the contract."""
    assert parse_action(_encoded({**ENVELOPE, "actionId": "lights.toggle"})) is None


def test_an_envelope_without_a_surface_id_is_rejected():
    envelope = {key: value for key, value in ENVELOPE.items() if key != "surfaceId"}
    assert parse_action(_encoded(envelope)) is None
    assert parse_action(_encoded({**ENVELOPE, "surfaceId": ""})) is None
