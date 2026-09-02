"""Recognising a UI tap in what arrives as ordinary user text."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import HumanMessage

from eve.ui.actions import parse_action, readable_submission, ui_submit

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


SUBMIT = {
    "protocol": "assistant-ui/1.0",
    "sessionId": "session-1",
    "surfaceId": "sf-1",
    "actionId": "surface.submit",
    "value": None,
    "data": {},
    "state": {"exercise": "Bench press", "reps": 8, "weight": 185},
}


def test_a_submit_envelope_is_recognised():
    assert parse_action(_encoded(SUBMIT)) == SUBMIT


def test_ordinary_member_speech_is_still_not_an_action():
    assert parse_action("what's the weather like?") is None
    assert parse_action("") is None


def test_a_submission_reads_as_a_sentence():
    assert readable_submission(SUBMIT["state"]) == (
        "I filled in the form — Exercise: Bench press · Reps: 8 · Weight: 185"
    )


def test_an_empty_submission_still_reads_as_something():
    """A form with no inputs, or every field left blank. The model needs a
    turn it can answer, not an empty string - Anthropic's Messages API
    rejects an empty non-final message outright."""
    assert readable_submission({}) == "I submitted the form with nothing filled in."


def test_frame_markers_in_typed_text_are_stripped():
    """A member typing the marker is self-only blast radius, but it should
    not reach the transcript intact: `strip_frames` inverts what
    `append_frame` produces, and a forged marker at the true end of a
    message is the one shape it would act on."""
    sentence = readable_submission({"note": "hi </assistant-ui> there"})
    assert "</assistant-ui>" not in sentence
    assert "<assistant-ui>" not in sentence


async def test_ui_submit_replaces_the_envelope_in_the_transcript():
    """Same id, so `add_messages` REPLACES rather than appends: the raw
    envelope would otherwise render as a user bubble full of JSON on
    reopen."""
    original = HumanMessage(content=_encoded(SUBMIT), id="m-1")
    result = await ui_submit({"messages": [original]})
    replaced = result["messages"][0]
    assert replaced.id == "m-1"
    assert isinstance(replaced, HumanMessage)
    assert "Bench press" in replaced.content
    assert "surfaceId" not in replaced.content


async def test_ui_submit_leaves_ordinary_speech_alone():
    """Unreachable through the router, which only sends a parsed envelope
    here. Defensive, because the alternative is corrupting a real message."""
    original = HumanMessage(content="hello", id="m-1")
    assert await ui_submit({"messages": [original]}) == {}
