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

import copy
import logging
import uuid

from eve.context import principal_sub
from eve.images import store
from eve.ui import protocol, stream

logger = logging.getLogger(__name__)

ROOT_CATALOG_ID = "column"


def new_surface_id() -> str:
    """Unique per card, not per thread. The client addresses a surface by
    this id and the action envelope carries it back, so nothing server-side
    has to remember it between turns."""
    return f"sf-{uuid.uuid4().hex[:8]}"


def build_create(
    surface_id: str, components: list, *, catalog_version: str = protocol.CATALOG_VERSION
) -> dict:
    """The `create` operation for one model-authored surface.

    `data` is empty and `localState` is unseeded, deliberately. Nothing
    fetches server-side data, so nothing produces `$data.` bindings - which
    removes a whole failure class, since an unresolvable binding renders the
    WHOLE-surface fallback rather than a partial tree. `localState` is the
    client's own presentation memory, restored from its cache on reopen; a
    value here would fight that restore for it.

    `catalog_version` defaults to the V1 baseline so every caller that
    predates `image` (EVE-21) keeps stamping exactly what it always did;
    `eve.ui.tools._show_surface` is the one caller that passes "2", and only
    when `prepare_images` actually found one.
    """
    return {
        "protocol": protocol.PROTOCOL,
        "op": "create",
        "surface": {
            "surfaceId": surface_id,
            "catalogId": ROOT_CATALOG_ID,
            "catalogVersion": catalog_version,
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


def aspect_of(width: int, height: int) -> str:
    """Bucket a stored image's real dimensions into one of the protocol's
    three aspect words, so the client can reserve layout space before the
    bytes themselves arrive. `>1.2`/`<1/1.2` gives a small dead zone around
    1:1 that both directions round down to `square`, rather than a coin-flip
    at exactly `ratio == 1`."""
    ratio = width / height if height else 1.0
    if ratio > 1.2:
        return "landscape"
    if ratio < 1 / 1.2:
        return "portrait"
    return "square"


def _as_text(node: dict, alt: str) -> dict:
    return {"id": node["id"], "type": "text", "properties": {"text": alt}}


async def prepare_images(components: list, config) -> tuple[list, bool]:
    """Fit every `image` in a model-authored tree to this client and this
    member BEFORE validation (spec 4.1, 4.2).

    Server-side provenance: the client drops an unfetchable image silently,
    so an id this member was never shown in this thread is caught here,
    logged, and shown as its alt text - which is the reason protocol.py
    exists at all. And server-side downgrade: an older phone gets a readable
    text line instead of a dropped surface.
    """
    if "image" not in component_types(components):
        return components, False
    tree = copy.deepcopy(components)
    can_render = protocol.IMAGE_VERSION in stream.catalog_versions(config)
    member_sub = principal_sub(config)
    thread_id = ((config or {}).get("configurable") or {}).get("thread_id")
    shown = False

    async def visit(nodes: list) -> None:
        nonlocal shown
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            props = node.get("properties")
            if node.get("type") == "image" and isinstance(props, dict) and isinstance(props.get("alt"), str):
                if not can_render:
                    nodes[index] = _as_text(node, props["alt"])
                    continue
                row = await store.resolve(str(props.get("imageId", "")), member_sub, thread_id) if member_sub else None
                if row is None:
                    logger.warning(
                        "image %s did not resolve for this member and thread; shown as text",
                        props.get("imageId"),
                    )
                    nodes[index] = _as_text(node, props["alt"])
                    continue
                props["imageId"] = row.id
                props.setdefault("aspect", aspect_of(row.width, row.height))
                shown = True
            children = node.get("children")
            if isinstance(children, list):
                await visit(children)

    await visit(tree)
    return tree, shown
