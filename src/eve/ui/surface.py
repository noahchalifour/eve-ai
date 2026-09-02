"""Assemble a model-authored component tree into a `create` operation.

The model supplies `components` and nothing else. Everything around the tree
- the surface id, the catalog id, the empty `data` and `localState` - is set
here, so the model has two fewer fields to get wrong and no decision to make
that it lacks the information to make.

`catalogId` is always `column`: the client's non-weather path already wraps
whatever it is given in a raised card, and the id doubles as a component type
in the shared catalog, so `column` is both legal and honest about the shape.

Pure module: no LangGraph, no I/O, no Eve state. `eve.ui.stream` owns the
emission, `eve.ui.tools` owns the tool.
"""

from __future__ import annotations

import uuid

from eve.ui import protocol

ROOT_CATALOG_ID = "column"


def new_surface_id() -> str:
    """Unique per card, not per thread. The client addresses a surface by
    this id and the action envelope carries it back, so nothing server-side
    has to remember it between turns."""
    return f"sf-{uuid.uuid4().hex[:8]}"


def build_create(surface_id: str, components: list) -> dict:
    """The `create` operation for one model-authored surface.

    `data` is empty and `localState` is unseeded, deliberately. Nothing
    fetches server-side data, so nothing produces `$data.` bindings - which
    removes a whole failure class, since an unresolvable binding renders the
    WHOLE-surface fallback rather than a partial tree. `localState` is the
    client's own presentation memory, restored from its cache on reopen; a
    value here would fight that restore for it.
    """
    return {
        "protocol": protocol.PROTOCOL,
        "op": "create",
        "surface": {
            "surfaceId": surface_id,
            "catalogId": ROOT_CATALOG_ID,
            "catalogVersion": protocol.CATALOG_VERSION,
            "components": components,
            "data": {},
            "localState": {},
        },
    }


def component_types(components: object) -> set[str]:
    """Every `type` in the tree, at any depth.

    Runs BEFORE `validate_operation`, on a tree that came straight from a
    model, so it tolerates any shape rather than raising: a malformed branch
    contributes nothing and the validator rejects it a moment later with a
    diagnostic the model can act on.

    Depth is unbounded here on purpose - `_validate_components` enforces
    MAX_DEPTH, and a tree deep enough to matter is rejected there. Recursing
    the whole thing first is what makes the gate honest: a nested input at a
    client that cannot render it is still a surface written permanently into
    that thread's transcript.
    """
    found: set[str] = set()
    stack = list(components) if isinstance(components, list) else []
    while stack:
        component = stack.pop()
        if not isinstance(component, dict):
            continue
        kind = component.get("type")
        if isinstance(kind, str):
            found.add(kind)
        children = component.get("children")
        if isinstance(children, list):
            stack.extend(children)
    return found
