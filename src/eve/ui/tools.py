"""The one tool the model gets: put a surface it composed on screen.

The asymmetry ADR 0014 drew - model decides WHETHER, server decides WHAT -
is gone, and ADR 0017 says why. What replaces it is narrower: the model
authors STRUCTURE, the server owns the envelope and the validation, and a
rejection comes back as a diagnostic the model can act on rather than the
silent drop the client would otherwise perform.

ADR 0017 also assumed the catalog would reach the model through
`search_skills`. It did not - see `eve.ui.schema` - so the legal component
types now ride in this tool's own argument schema, built per client from
what that client declared. The gates below are unchanged: a schema steers a
model, it never guarantees one, so every tree is still checked.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

from eve.ui import protocol, schema, stream, surface

_NO_CLIENT_SUPPORT = (
    "This member's app cannot render {missing}. Answer in words instead, or "
    "build the surface again using only the components it does support."
)
_NO_SUCH_COMPONENT = (
    "No such component: {unknown}. The catalog is exactly these, "
    "case-sensitive: {catalog}.\n{hint}\n"
    "Rebuild the tree using only those and call show_surface again."
)
_MISSING_IDENTITY = (
    "A component in this tree is missing its `id` or its `type`, or gave a "
    "non-string for one of them.\n{hint}\n"
    "Fix the tree and call show_surface again."
)
_REJECTED_WITH_HINT = (
    "The surface was rejected: {error}.\n{hint}\n"
    "Fix the tree and call show_surface again."
)
_REJECTED = "The surface was rejected before it could be shown. Answer in words instead."

_STRUCTURE = (
    "Every component needs a unique string `id` and a `type`; layout types "
    "take `children`. The shape is "
    '{"id": ..., "type": ..., "properties": {...}, "children": [...]}. '
    "Legal properties for the types you used:"
)

_DESCRIPTION = """Put an interactive UI on screen: a form, a tracker, a summary card.

`components` is a tree of typed components; the legal types and their
properties are in this tool's own schema, so you can build one without
looking anything up. Search your skills for "build a UI" when you want the
guidance on composing a GOOD one - do it in the same round as any tool call
you need for the data, not after it.

Inputs write to the surface's local state, and a Save button hands that
state back to you as a new turn.

Prefer this over prose when the member wants to enter, track, or compare
something. Prefer prose when the answer is a sentence."""


def schema_hint(types: set[str]) -> str:
    """The required structure, plus the legal properties for just the types
    the model used.

    Scoped rather than complete to keep the hint short, and SELF-SUFFICIENT
    so a retry depends on nothing else. The structure line leads because the
    property table alone is silent about the most common defect: a missing
    `id` is reported by the validator as `string`, and a hint that listed
    only properties never mentioned the field that was actually wrong.
    """
    lines = []
    for kind in sorted(types):
        allowed = protocol._ALLOWED_PROPERTIES.get(kind)
        if allowed is None:
            continue
        properties = ", ".join(sorted(allowed)) if allowed else "none"
        lines.append(f"{kind}: {properties}")
    if not lines:
        return _STRUCTURE
    return "\n".join([_STRUCTURE, *lines])


async def _show_surface(
    components: list, config: RunnableConfig
) -> tuple[str, dict | None]:
    components, has_image = await surface.prepare_images(components, config)
    requested = surface.component_types(components)
    unknown = requested - protocol.CATALOG_IDS
    if unknown:
        # Not a client problem, and saying it was one is what OPENA-17 hit:
        # a model that authored `Text`/`Checkbox` was told the member's app
        # could not render them, which is unactionable and reads as a phone
        # too old. Naming the catalog AND the properties makes the retry
        # self-sufficient - naming only the catalog, as this did before, got
        # the type names fixed and then failed again on the properties.
        return (
            _NO_SUCH_COMPONENT.format(
                unknown=", ".join(sorted(unknown)),
                catalog=", ".join(sorted(protocol.CATALOG_IDS)),
                hint=schema_hint(requested & protocol.CATALOG_IDS),
            ),
            None,
        )
    if not stream.supports(config, requested):
        declared = stream.capabilities(config) or {}
        ids = declared.get("catalogIds")
        missing = (
            sorted(requested - set(ids))
            if isinstance(ids, list)
            else sorted(requested)
        )
        return (_NO_CLIENT_SUPPORT.format(missing=", ".join(missing) or "surfaces"), None)

    operation = surface.build_create(
        surface.new_surface_id(),
        components,
        catalog_version=protocol.IMAGE_VERSION if has_image else protocol.CATALOG_VERSION,
    )
    error = protocol.validate_operation(operation)
    if error is not None:
        # The client rejects SILENTLY - one neutral "This content can't be
        # shown" card, or a dropped frame with a log line that never leaves
        # the phone. This returned string is the entire feedback channel, and
        # it is what ADR 0014's strongest objection turned on.
        #
        # `string` gets its own message. That code is what `_validate_components`
        # returns for a missing or non-string `id`/`type`, and it is the most
        # common defect a model hits - but the word "string" names a type
        # rather than a field, so the generic form left the model guessing at
        # exactly the moment it needed telling.
        if error == "string":
            return (_MISSING_IDENTITY.format(hint=schema_hint(requested)), None)
        return (
            _REJECTED_WITH_HINT.format(error=error, hint=schema_hint(requested)),
            None,
        )
    if not stream.emit(operation):
        return (_REJECTED, None)
    return (
        "Surface shown. Say one short sentence about it; do not read it out.",
        operation,
    )


def build_show_surface(catalog_ids: frozenset[str] | set[str]) -> StructuredTool:
    """`show_surface`, described by the catalog `catalog_ids` allows.

    Built per call rather than once at import, because the schema depends on
    what the CONNECTED CLIENT declared - `eve.graph._static_tools` is already
    rebuilt per model call for exactly this kind of per-turn answer, and
    building one is a dict construction with no I/O.

    A `StructuredTool` over a raw JSON-Schema dict rather than the `@tool`
    decorator: the decorator infers a pydantic model from the signature,
    which cannot express the closed per-type property sets. Verified against
    langchain-core 1.6.0 - a dict `args_schema` reaches the model intact,
    still injects `RunnableConfig`, and still honours
    `response_format="content_and_artifact"`.
    """
    return StructuredTool.from_function(
        coroutine=_show_surface,
        name="show_surface",
        description=_DESCRIPTION,
        args_schema=schema.components_schema(catalog_ids),
        response_format="content_and_artifact",
    )


# The whole-catalog tool. `eve.graph` builds a client-scoped one per turn;
# this is what imports and tests that do not model a specific client use.
show_surface = build_show_surface(protocol.CATALOG_IDS)
