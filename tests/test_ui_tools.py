"""`show_weather`: the model's entire share of the dynamic UI feature."""

from __future__ import annotations

import json

import pytest

from eve.ui import protocol, stream, tools

CONFIG = {
    "configurable": {
        "assistant_ui": {
            "protocol": "assistant-ui/1.0",
            "catalogVersion": "1",
            "catalogIds": ["weather"],
        }
    }
}

STATE = {
    "messages": [],
    "member": {
        "sub": "sub-noah",
        "name": "Noah",
        "role": "adult",
        "timezone": "America/Toronto",
        "permissions": [],
        "local_time": "2026-08-31 14:00 EDT",
    },
    "system_prompt": "",
    "memory": None,
    "dynamic_tools": [],
}

PAYLOAD = {
    "entity_id": "weather.home",
    "location": "Home",
    "condition": "partlycloudy",
    "temperature": 21.4,
    "hourly": [
        {"datetime": "2026-08-31T18:00:00+00:00", "condition": "sunny", "temperature": 22}
    ],
    "daily": [],
}


@pytest.fixture
def written(monkeypatch):
    frames: list = []
    monkeypatch.setattr(stream, "get_stream_writer", lambda: frames.append)
    return frames


def _serve(monkeypatch, raw: str):
    async def fake_invoke(tool, arguments, **kwargs):
        assert tool == "home.weather"
        return raw

    monkeypatch.setattr(tools, "invoke", fake_invoke)


async def _call(config=CONFIG):
    """`state` rides in `args`, not in the config: `InjectedState` is hidden
    from `tool_call_schema` (so the model sees a zero-argument tool) but is
    still required by `args_schema`, which is what a direct invoke validates
    against."""
    return await tools.show_weather.ainvoke(
        {
            "name": "show_weather",
            "args": {"state": STATE},
            "id": "c1",
            "type": "tool_call",
        },
        config=config,
    )


def test_the_model_sees_a_tool_with_no_arguments():
    """A tool the model has to fill in is a tool the model can get wrong.
    Neither the injected state nor the config may appear in the call schema."""
    assert tools.show_weather.tool_call_schema.model_json_schema()["properties"] == {}


async def test_it_emits_one_valid_create_and_returns_a_short_sentence(
    monkeypatch, written
):
    _serve(monkeypatch, json.dumps(PAYLOAD))

    message = await _call()

    assert len(written) == 1
    operation = written[0]["assistant_ui"]
    assert protocol.validate_operation(operation) is None
    assert operation["op"] == "create"
    assert "Home" in message.content
    assert message.artifact == operation


async def test_the_artifact_is_the_operation_so_it_can_be_persisted(
    monkeypatch, written
):
    """`persist_ui` copies this artifact into the AI message as a portable
    frame. Without it a reopened session shows the turn with no card, because
    `custom` frames are streamed and never stored. It rides as an ARTIFACT so
    the surface JSON never enters the model's own context."""
    _serve(monkeypatch, json.dumps(PAYLOAD))

    message = await _call()

    assert message.artifact["surface"]["catalogId"] == "weather"
    assert "surfaceId" not in message.content


async def test_the_forecast_labels_use_the_members_own_timezone(monkeypatch, written):
    """18:00Z is 2 PM in Toronto. A UTC label would be wrong for every member
    not at UTC+0, and the timezone is only knowable from injected state."""
    _serve(monkeypatch, json.dumps(PAYLOAD))

    await _call()

    cells = written[0]["assistant_ui"]["surface"]["data"]["hourly"]
    assert cells[0]["label"] == "2 PM"


async def test_a_client_that_declared_nothing_gets_prose_and_no_frame(
    monkeypatch, written
):
    async def fake_invoke(tool, arguments, **kwargs):  # pragma: no cover
        raise AssertionError("must not reach Home Assistant")

    monkeypatch.setattr(tools, "invoke", fake_invoke)

    message = await _call(config={"configurable": {}})

    assert written == []
    assert message.artifact is None
    assert "words" in message.content


async def test_an_eve_tools_failure_degrades_to_prose(monkeypatch, written):
    """The global constraint every Eve tool obeys: a failing external system
    becomes a returned string the model can talk around, never an exception
    that kills the turn."""
    _serve(monkeypatch, "error: eve-tools unavailable (ConnectError)")

    message = await _call()

    assert written == []
    assert message.artifact is None
    assert "weather" in message.content.lower()


async def test_a_rejected_operation_degrades_to_prose(monkeypatch, written):
    """The last line of defence. If a template change ever produces an
    operation the client would refuse, Eve still answers - she just answers in
    words."""
    _serve(monkeypatch, json.dumps(PAYLOAD))
    monkeypatch.setattr(stream, "emit", lambda operation: False)

    message = await _call()

    assert message.artifact is None
    assert "words" in message.content
