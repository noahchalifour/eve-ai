"""`eve.ui.schema` is a PROJECTION of the server validator, not a sixth
hand-written copy of the catalog. These tests are what make that claim true:
every assertion below reads `eve.ui.protocol`'s own tables rather than a
literal list, so a catalog change that forgets this module fails here.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from eve.ui import protocol, schema


def _component_items(document: dict) -> dict:
    return document["properties"]["components"]["items"]


def _branches(document: dict) -> dict[str, dict]:
    items = _component_items(document)
    return {b["title"]: b for b in items["properties"]["properties"]["anyOf"]}


@pytest.fixture
def full() -> dict:
    return schema.components_schema(protocol.CATALOG_IDS)


def test_every_catalog_type_is_offered(full):
    assert set(_component_items(full)["properties"]["type"]["enum"]) == set(
        protocol.CATALOG_IDS
    )


def test_every_branch_matches_the_validator_exactly(full):
    """The property tables are the thing most likely to drift, and a drift
    either advertises a property the validator rejects (a guaranteed
    `component-schema` round trip) or hides one that is legal."""
    branches = _branches(full)
    assert set(branches) == set(protocol.CATALOG_IDS)
    for kind, branch in branches.items():
        assert set(branch["properties"]) == set(protocol._ALLOWED_PROPERTIES[kind]), kind
        # Mirrors `_validate_properties` rejecting an undeclared key.
        assert branch["additionalProperties"] is False, kind


def test_id_and_type_are_required(full):
    """The regression this whole module exists for. A model that omitted
    `id` got `The surface was rejected: string` plus a property hint that
    never mentioned `id` - unfixable from the message alone."""
    assert _component_items(full)["required"] == ["id", "type"]


def test_the_value_constraints_match_the_validator(full):
    branches = _branches(full)
    columns = branches["grid"]["properties"]["columns"]
    assert (columns["minimum"], columns["maximum"]) == (1, 6)
    assert branches["expandable"]["properties"]["expanded"]["type"] == "boolean"
    assert branches["button"]["properties"]["actionId"]["const"] in protocol.ACTION_IDS


def test_a_restricted_client_gets_a_restricted_schema():
    """An older client declaring three ids is DESCRIBED by three ids, rather
    than being refused after the fact by `stream.supports`."""
    document = schema.components_schema({"card", "text"})
    assert _component_items(document)["properties"]["type"]["enum"] == ["card", "text"]
    assert set(_branches(document)) == {"card", "text"}


def test_an_unknown_declared_id_is_dropped():
    """Intersected with the server catalog, so a client advertising a type
    this server cannot validate never reaches the model."""
    document = schema.components_schema({"card", "Checkbox"})
    assert _component_items(document)["properties"]["type"]["enum"] == ["card"]


def test_children_are_expanded_inline_not_by_reference(full):
    """`convert_to_openai_tool` resolves `$defs`/`$ref` and flattens a
    recursive reference to a bare `{}`, which would delete every constraint
    below the first level. Inline expansion is the only shape that survives."""
    child = _component_items(full)["properties"]["children"]["items"]
    assert child["required"] == ["id", "type"]
    grandchild = child["properties"]["children"]["items"]
    assert grandchild["required"] == ["id", "type"]


def test_the_inline_expansion_terminates(full):
    """Past `MAX_INLINE_DEPTH` a deeper tree is still legal - the validator
    allows `MAX_DEPTH` - it is simply no longer described."""
    node = _component_items(full)
    for _ in range(schema.MAX_INLINE_DEPTH - 1):
        node = node["properties"]["children"]["items"]
    assert node["properties"]["children"]["items"] == {"type": "object"}


def test_the_schema_survives_conversion_to_a_tool_call(full):
    """The trip that actually matters: whatever langchain hands the model.
    An enum lost here is a catalog the model never sees."""

    async def _call(components):  # pragma: no cover - never invoked
        return "unused"

    tool = StructuredTool.from_function(
        coroutine=_call, name="show_surface", description="d", args_schema=full
    )
    blob = json.dumps(convert_to_openai_tool(tool))
    assert "$ref" not in blob and "$defs" not in blob
    for kind in protocol.CATALOG_IDS:
        assert kind in blob
    assert "additionalProperties" in blob
    assert sorted(protocol.ACTION_IDS)[0] in blob


def test_the_schema_stays_within_its_context_budget(full):
    """It rides in context on every turn a capable client is connected. The
    ceiling is generous but it fails loudly if `MAX_INLINE_DEPTH` is raised
    without anyone pricing it - depth 8 is ~21 KiB."""
    assert len(json.dumps(full, separators=(",", ":"))) < 12_000


def test_openai_structured_output_uses_anyof_never_oneof(full):
    """OpenAI's structured-output subset supports `anyOf` and rejects
    `oneOf`. The two are equivalent for these mutually-exclusive branches,
    so the portable spelling is free."""
    blob = json.dumps(full)
    assert "oneOf" not in blob
    assert "anyOf" in blob


def test_the_image_branch_says_what_the_validator_enforces(full):
    image = _branches(full)["image"]
    assert image["required"] == ["alt", "imageId"]
    assert set(image["properties"]["aspect"]["enum"]) == set(protocol.IMAGE_ASPECTS)
    # The short form is accepted from the model and rewritten to the full id
    # by eve.ui.surface.prepare_images before validation.
    assert "[image" in image["properties"]["imageId"]["description"]
