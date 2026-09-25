"""The catalog, expressed as the `show_surface` argument schema.

ADR 0017 put the component catalog in `skills/build-a-ui/SKILL.md` and had
the tool's docstring point the model at `search_skills` to retrieve it. That
makes correctness depend on a semantic retrieval succeeding BEFORE the first
call - and when the corpus was unreachable (it was, in the built image, for
the whole life of the deployment) the model had no catalog at all and
authored React names: `Button`, `Card`, `DateInput`.

A JSON Schema does not have to be retrieved. Every legal component type
arrives as tool-call grammar, on the one call where it is needed, so the
first attempt is already informed. The skill stays for what a schema cannot
carry: when a surface is the right answer at all, and how to compose a good
one.

Derived ENTIRELY from `eve.ui.protocol`'s tables - `CATALOG_IDS`,
`_ALLOWED_PROPERTIES` and the constraints in `_validate_property`. This is
not a sixth hand-synced copy of the catalog; it is a projection of the
server validator, and `tests/test_ui_schema.py` pins it to that validator
field by field.

Pure module: no LangGraph, no I/O, no Eve state - the same contract
`protocol.py` and `surface.py` keep.
"""

from __future__ import annotations

from eve.ui import protocol

# How deep `children` is expanded inline. NOT `$ref` recursion: langchain's
# `convert_to_openai_tool` resolves `$defs`/`$ref` and flattens a recursive
# reference to a bare `{}`, which would silently delete every constraint
# below the first level - verified against langchain-core 1.6.0. An inline
# expansion is the only shape that survives the trip to the model.
#
# Three, not `protocol.MAX_DEPTH` (8): the schema's serialized size roughly
# multiplies per level, and this sits in context on every turn a capable
# client is connected. The skill already tells the model to keep a surface to
# one card and a handful of rows, and `_validate_components` still enforces
# the true depth - a deeper tree is legal, it is simply described as a plain
# object past this point rather than spelled out.
MAX_INLINE_DEPTH = 3


def components_schema(catalog_ids: frozenset[str] | set[str]) -> dict:
    """The raw JSON Schema for `show_surface`'s `components` argument,
    restricted to `catalog_ids`.

    `catalog_ids` is the intersection of the server catalog and what the
    connected client declared in `config.configurable.assistant_ui`, so an
    older client is described by a smaller schema rather than being told
    `no` after the fact by `stream.supports`.
    """
    legal = sorted(set(catalog_ids) & protocol.CATALOG_IDS)
    return {
        "type": "object",
        "properties": {"components": _component_array(legal, MAX_INLINE_DEPTH)},
        "required": ["components"],
    }


def _component_array(legal: list[str], depth: int) -> dict:
    return {
        "type": "array",
        "description": (
            "A tree of typed components. Every component needs a unique `id` "
            "and a `type`."
        ),
        "items": _component(legal, depth),
    }


def _component(legal: list[str], depth: int) -> dict:
    # The bottom of the inline expansion. `{"type": "object"}` rather than
    # omitting `children` entirely: a deeper tree is still LEGAL (the
    # validator allows MAX_DEPTH), it is just no longer described.
    if depth <= 0:
        return {"type": "object"}
    return {
        "type": "object",
        "required": ["id", "type"],
        "properties": {
            "id": {
                "type": "string",
                "description": "Unique within this surface, e.g. `reps`.",
            },
            "type": {"type": "string", "enum": legal},
            "properties": _properties(legal),
            "children": {
                "type": "array",
                "description": "Child components, for layout types.",
                "items": _component(legal, depth - 1),
            },
        },
    }


def _properties(legal: list[str]) -> dict:
    """One closed object per legal type.

    A single flat object would advertise every property to every type and so
    describe exactly the error this schema exists to prevent - a `stateKey`
    on a `card`. `additionalProperties: false` mirrors `_validate_properties`
    rejecting an undeclared key with `component-schema`.

    `anyOf`, not `oneOf`: the two are equivalent here (the branches are
    mutually exclusive - each names a different closed property set), but
    OpenAI's structured-output subset supports `anyOf` and explicitly
    rejects `oneOf`. The cheaper of two identical spellings is the portable
    one.
    """
    return {
        "description": "Properties legal for this component's `type`.",
        "anyOf": [_properties_for(kind) for kind in legal],
    }


def _properties_for(kind: str) -> dict:
    allowed = sorted(protocol._ALLOWED_PROPERTIES.get(kind, frozenset()))
    document = {
        "title": kind,
        "type": "object",
        "additionalProperties": False,
        "properties": {name: _property(name) for name in allowed},
    }
    if kind == "image":
        # Mirrors `_validate_component` rejecting an image missing either key
        # with `component-schema` - said up front rather than discovered by
        # a rejected surface.
        document["required"] = ["alt", "imageId"]
    return document


def _property(name: str) -> dict:
    """The value constraints `protocol._validate_property` enforces, said in
    schema rather than discovered by rejection."""
    if name == "columns":
        return {"type": "integer", "minimum": 1, "maximum": 6}
    if name == "expanded":
        return {"type": "boolean"}
    if name == "actionId":
        # The only action there is. `const` rather than a free string,
        # because `_validate_property` returns `action-schema` for anything
        # else and a model has no way to guess the vocabulary.
        return {
            "type": "string",
            "const": sorted(protocol.ACTION_IDS)[0],
            "description": "Hands this surface's local state back to Eve.",
        }
    if name == "setState":
        return {
            "type": "object",
            "description": (
                "Literal local-state values to write on tap. A button must "
                "have exactly one of `actionId` or `setState`."
            ),
        }
    if name == "stateKey":
        return {
            "type": "string",
            "description": (
                "The local-state key this input writes. A literal name, "
                "never a `$data.` binding."
            ),
        }
    if name == "options":
        return {"type": "array", "items": {"type": "string"}}
    if name == "imageId":
        return {
            "type": "string",
            "description": (
                "The id of an image you were shown, as it appeared: "
                "`[image 3f2a9c01]` means imageId `3f2a9c01`. Never invent one."
            ),
        }
    if name == "aspect":
        return {"type": "string", "enum": sorted(protocol.IMAGE_ASPECTS)}
    if name == "alt":
        return {"type": "string", "description": "What the image shows, in a few words."}
    return {"type": "string"}
