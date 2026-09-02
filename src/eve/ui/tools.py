"""The one tool the model gets: put a surface it composed on screen.

The asymmetry ADR 0014 drew - model decides WHETHER, server decides WHAT -
is gone, and ADR 0017 says why. What replaces it is narrower: the model
authors STRUCTURE, the server owns the envelope and the validation, and a
rejection comes back as a diagnostic the model can act on rather than the
silent drop the client would otherwise perform.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from eve.ui import protocol, stream, surface

_NO_CLIENT_SUPPORT = (
    "This member's app cannot render {missing}. Answer in words instead, or "
    "build the surface again using only the components it does support."
)
_REJECTED = "The surface was rejected before it could be shown. Answer in words instead."


def schema_hint(types: set[str]) -> str:
    """The legal properties for just the types the model used.

    Scoped rather than complete for two reasons: it keeps the hint near 50
    tokens, and it makes the retry path SELF-SUFFICIENT. `search_skills`
    ranks semantically over a growing corpus and can miss, so a rejection
    that merely pointed at `build-a-ui` would make correctness depend on a
    retrieval succeeding twice. This depends on none.
    """
    lines = []
    for kind in sorted(types):
        allowed = protocol._ALLOWED_PROPERTIES.get(kind)
        if allowed is None:
            continue
        properties = ", ".join(sorted(allowed)) if allowed else "none"
        lines.append(f"{kind}: {properties}")
    return "\n".join(lines)


@tool
async def show_surface(
    components: list, config: RunnableConfig
) -> tuple[str, dict | None]:
    """Put an interactive UI on screen: a form, a tracker, a summary card.

    `components` is a tree of typed components in the `assistant-ui` catalog.
    Search your skills for "build a UI" FIRST to get the component catalog
    and how to compose a good one - do it in the same round as any tool call
    you need for the data, not after it. Inputs write to the surface's local
    state, and a Save button hands that state back to you as a new turn.

    Prefer this over prose when the member wants to enter, track, or compare
    something. Prefer prose when the answer is a sentence.
    """
    requested = surface.component_types(components)
    if not stream.supports(config, requested):
        declared = stream.capabilities(config) or {}
        ids = declared.get("catalogIds")
        missing = (
            sorted(requested - set(ids))
            if isinstance(ids, list)
            else sorted(requested)
        )
        return (_NO_CLIENT_SUPPORT.format(missing=", ".join(missing) or "surfaces"), None)

    operation = surface.build_create(surface.new_surface_id(), components)
    error = protocol.validate_operation(operation)
    if error is not None:
        # The client rejects SILENTLY - one neutral "This content can't be
        # shown" card, or a dropped frame with a log line that never leaves
        # the phone. This returned string is the entire feedback channel, and
        # it is what ADR 0014's strongest objection turned on.
        return (
            f"The surface was rejected: {error}. Legal properties for the "
            f"types you used:\n{schema_hint(requested)}\n"
            "Fix the tree and call show_surface again.",
            None,
        )
    if not stream.emit(operation):
        return (_REJECTED, None)
    return (
        "Surface shown. Say one short sentence about it; do not read it out.",
        operation,
    )


# Mark this tool as returning both content and artifact, for agent/model binding.
# This tells the model (when bound in an agent) that this tool returns structured
# output with both content and an artifact component, enabling rich tool results.
show_surface.response_format = "content_and_artifact"
