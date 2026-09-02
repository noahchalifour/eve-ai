"""`show_surface`: the model's entire share of the dynamic UI feature."""

from __future__ import annotations

import pytest

from eve.ui import protocol, stream, tools

CONFIG = {
    "configurable": {
        "assistant_ui": {
            "protocol": "assistant-ui/1.0",
            "catalogVersion": "1",
            "catalogIds": [
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
                "textField",
                "numberField",
            ],
        }
    }
}

OLD_CLIENT = {
    "configurable": {
        "assistant_ui": {
            "protocol": "assistant-ui/1.0",
            "catalogVersion": "1",
            "catalogIds": ["column", "card", "text"],
        }
    }
}

TRACKER = [
    {
        "id": "c1",
        "type": "card",
        "properties": {"title": "Workout"},
        "children": [
            {
                "id": "c2",
                "type": "numberField",
                "properties": {"stateKey": "reps", "label": "Reps"},
            },
            {
                "id": "c3",
                "type": "button",
                "properties": {"label": "Save", "actionId": "surface.submit"},
            },
        ],
    }
]


@pytest.fixture
def written(monkeypatch):
    frames: list = []
    monkeypatch.setattr(stream, "get_stream_writer", lambda: frames.append)
    return frames


async def test_a_valid_tree_is_emitted_and_returned_as_an_artifact(written):
    # Invoke via coroutine to get both content and artifact (what ToolNode does internally)
    content, artifact = await tools.show_surface.coroutine(TRACKER, CONFIG)
    assert len(written) == 1
    assert written[0]["assistant_ui"]["op"] == "create"
    assert artifact is not None
    assert protocol.validate_operation(artifact) is None
    assert "shown" in content.lower()


async def test_an_invalid_tree_returns_a_diagnostic_the_model_can_act_on(written):
    """The client rejects SILENTLY, so this returned string is the only
    feedback that exists. It names the code and the legal properties for the
    types the model actually used - self-contained, so the retry needs no
    second skills lookup."""
    bad = [
        {
            "id": "c1",
            "type": "numberField",
            "properties": {"stateKey": "reps", "placeholder": "8"},
        }
    ]
    content, artifact = await tools.show_surface.coroutine(bad, CONFIG)
    assert artifact is None
    assert written == []
    assert "component-schema" in content
    assert "stateKey" in content
    assert "numberField" in content


async def test_an_old_client_is_refused_only_the_types_it_lacks(written):
    content, artifact = await tools.show_surface.coroutine(TRACKER, OLD_CLIENT)
    assert artifact is None
    assert written == []
    assert "numberField" in content

    plain = [{"id": "c1", "type": "text", "properties": {"text": "Hello"}}]
    _, artifact = await tools.show_surface.coroutine(plain, OLD_CLIENT)
    assert artifact is not None


async def test_a_client_that_declared_nothing_gets_words(written):
    content, artifact = await tools.show_surface.coroutine(TRACKER, {})
    assert artifact is None
    assert written == []
    assert "cannot" in content.lower() or "can't" in content.lower()


async def test_a_rejected_emission_never_returns_an_artifact(monkeypatch):
    """`stream.emit` returns False outside a runnable context. Returning the
    artifact anyway would make `persist_ui` write a frame for a card the
    member never saw."""
    monkeypatch.setattr(tools.stream, "emit", lambda operation: False)
    content, artifact = await tools.show_surface.coroutine(TRACKER, CONFIG)
    assert artifact is None
    assert "words" in content.lower()


def test_the_schema_hint_covers_only_the_types_asked_for():
    """Properties are sorted, so the hint is stable across runs - a model
    retrying should not see the schema reshuffle between attempts."""
    hint = tools.schema_hint({"numberField", "button"})
    assert "numberField: label, stateKey" in hint
    assert "button: actionId, actionValue, label, setState" in hint
    assert "grid" not in hint


def test_the_schema_hint_ignores_unknown_types():
    """An unknown type is already rejected as `component-type`; the hint
    must not raise trying to describe it."""
    assert tools.schema_hint({"nonsense"}) == ""


def test_the_docstring_carries_no_property_table():
    """The catalog lives in `skills/build-a-ui/SKILL.md`, retrieved on
    demand. A table here would be in context on every turn a capable client
    is connected, whether or not a UI is wanted."""
    doc = tools.show_surface.description
    assert "stateKey" not in doc
    assert "numberField" not in doc
    # Generous, but it fails loudly if someone pastes the table back in.
    assert len(doc) < 900
