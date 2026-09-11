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
    # Invoke via ainvoke with ToolCall-shaped input (how ToolNode invokes tools in production)
    result = await tools.show_surface.ainvoke(
        {"type": "tool_call", "name": "show_surface", "args": {"components": TRACKER}, "id": "test-1"},
        config=CONFIG,
    )
    # Result is a ToolMessage with content and artifact attributes
    content = result.content
    artifact = result.artifact
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
    result = await tools.show_surface.ainvoke(
        {"type": "tool_call", "name": "show_surface", "args": {"components": bad}, "id": "test-2"},
        config=CONFIG,
    )
    content = result.content
    artifact = result.artifact
    assert artifact is None
    assert written == []
    assert "component-schema" in content
    assert "stateKey" in content
    assert "numberField" in content


async def test_a_component_without_an_id_is_told_it_needs_one(written):
    """The reported bug's SECOND failure. A model that fixed its invented
    type names still omitted `id`, and got back `The surface was rejected:
    string` - the validator's code for a missing or non-string `id`/`type` -
    alongside a hint that listed properties and never once said `id`. The
    model could not converge from that, and burned the turn's tool budget
    retrying.

    `string` names a TYPE, not a field. This message names the field."""
    missing_id = [
        {
            "type": "card",
            "properties": {"title": "Workout"},
            "children": [
                {"type": "button", "properties": {"label": "Save", "actionId": "surface.submit"}}
            ],
        }
    ]
    result = await tools.show_surface.ainvoke(
        {
            "type": "tool_call",
            "name": "show_surface",
            "args": {"components": missing_id},
            "id": "test-id",
        },
        config=CONFIG,
    )
    assert result.artifact is None
    assert written == []
    assert "`id`" in result.content
    # The bare code alone was the unactionable form; it must not be the
    # whole message any more.
    assert "rejected: string" not in result.content


async def test_an_old_client_is_refused_only_the_types_it_lacks(written):
    result = await tools.show_surface.ainvoke(
        {"type": "tool_call", "name": "show_surface", "args": {"components": TRACKER}, "id": "test-3"},
        config=OLD_CLIENT,
    )
    content = result.content
    artifact = result.artifact
    assert artifact is None
    assert written == []
    assert "numberField" in content

    plain = [{"id": "c1", "type": "text", "properties": {"text": "Hello"}}]
    result2 = await tools.show_surface.ainvoke(
        {"type": "tool_call", "name": "show_surface", "args": {"components": plain}, "id": "test-4"},
        config=OLD_CLIENT,
    )
    artifact2 = result2.artifact
    assert artifact2 is not None


async def test_a_client_that_declared_nothing_gets_words(written):
    result = await tools.show_surface.ainvoke(
        {"type": "tool_call", "name": "show_surface", "args": {"components": TRACKER}, "id": "test-5"},
        config={},
    )
    content = result.content
    artifact = result.artifact
    assert artifact is None
    assert written == []
    assert "cannot" in content.lower() or "can't" in content.lower()


async def test_a_rejected_emission_never_returns_an_artifact(monkeypatch):
    """`stream.emit` returns False outside a runnable context. Returning the
    artifact anyway would make `persist_ui` write a frame for a card the
    member never saw."""
    monkeypatch.setattr(tools.stream, "emit", lambda operation: False)
    result = await tools.show_surface.ainvoke(
        {"type": "tool_call", "name": "show_surface", "args": {"components": TRACKER}, "id": "test-6"},
        config=CONFIG,
    )
    content = result.content
    artifact = result.artifact
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
    must not raise trying to describe it, and must not invent a property
    table for it.

    It still returns the STRUCTURE line. The case that reaches here is a
    tree built entirely of invented types, where `id`/`type` is the only
    thing left worth saying - returning "" there, as this used to, spent the
    one message the model gets on nothing."""
    hint = tools.schema_hint({"nonsense"})
    assert "nonsense" not in hint
    assert "`id`" in hint


def test_the_docstring_carries_no_property_table():
    """The catalog reaches the model through the ARGUMENT SCHEMA now
    (`eve.ui.schema`), where it is machine-checkable and scoped to what the
    client declared. Restating it in prose here would be a second copy that
    no validator guards, and prose is the copy that drifts."""
    doc = tools.show_surface.description
    assert "stateKey" not in doc
    assert "numberField" not in doc
    # Generous, but it fails loudly if someone pastes the table back in.
    assert len(doc) < 900


def test_the_catalog_reaches_the_model_through_the_argument_schema():
    """The fix for the reported bug, stated as an invariant: a model that
    reads only the tool definition - no skills search, no retry - can still
    see every legal component type and that `id` is required."""
    document = tools.show_surface.args_schema
    component = document["properties"]["components"]["items"]
    assert set(component["properties"]["type"]["enum"]) == set(protocol.CATALOG_IDS)
    assert component["required"] == ["id", "type"]


def test_a_client_scoped_tool_advertises_only_that_client_s_catalog():
    tool = tools.build_show_surface({"card", "text"})
    component = tool.args_schema["properties"]["components"]["items"]
    assert component["properties"]["type"]["enum"] == ["card", "text"]
    assert tool.name == "show_surface"


async def test_an_invented_component_names_the_catalog_not_the_client(written):
    """OPENA-17: a model authoring `Text`/`Checkbox` was told the member's
    app could not render them - unactionable, and false. The retry has to be
    self-sufficient, so the message names the catalog."""
    invented = [
        {
            "id": "c1",
            "type": "Card",
            "properties": {"title": "Workout"},
            "children": [{"id": "c2", "type": "Checkbox", "properties": {}}],
        }
    ]
    result = await tools.show_surface.ainvoke(
        {
            "type": "tool_call",
            "name": "show_surface",
            "args": {"components": invented},
            "id": "test-7",
        },
        config=CONFIG,
    )
    assert result.artifact is None
    assert written == []
    assert "Card, Checkbox" in result.content
    assert "cannot render" not in result.content
    assert "segmentedSelection" in result.content
    # Naming only the catalog got the TYPE names fixed and then failed again
    # on properties and the missing `id`, one round trip later. The message
    # now carries both, so one correction is enough.
    assert "`id`" in result.content
