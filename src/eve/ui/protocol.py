"""The server side of the `assistant-ui/1.0` wire contract.

A mirror of the client's own validators (flutter-open-assistant,
`lib/data/services/agent/dynamic_surface_protocol.dart` and
`lib/domain/models/dynamic_ui/dynamic_surface.dart`). Two copies of one
validator is normally a smell. Here it is the only feedback we get: the client
rejects SILENTLY - a surface that fails validation renders one neutral "This
content can't be shown" card, and on the `custom` path is dropped with a log
line that never leaves the phone. Validating before we emit turns that
invisible drop into a server-side diagnostic.

Pure module: no LangGraph, no I/O, no Eve state. `eve.ui.stream` owns the
emission, `eve.ui.surface` owns the model-authored surface builder.
"""

from __future__ import annotations

import json
import re

PROTOCOL = "assistant-ui/1.0"
CATALOG_VERSION = "1"

# The closed V1 catalog. The same twelve ids are legal as a surface's
# `catalogId` AND as a component's `type` - the client checks both against one
# set (`DynamicSurfaceProtocol._componentTypes`), so this file does too.
CATALOG_IDS = frozenset(
    {
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
    }
)

# Emptied with the weather surface. Task 2 refills it with `surface.submit`.
ACTION_IDS: frozenset[str] = frozenset()

MAX_SURFACES_PER_TURN = 8
MAX_COMPONENTS = 64
MAX_DEPTH = 8
MAX_STRING = 2048
MAX_DEFINITION_BYTES = 48 * 1024
MAX_PATCH_BYTES = 16 * 1024
MAX_UPDATES_PER_MINUTE = 30

OPENING_MARKER = "<assistant-ui>\n"
CLOSING_MARKER = "\n</assistant-ui>"

_ALLOWED_PROPERTIES: dict[str, frozenset[str]] = {
    "card": frozenset({"title"}),
    "grid": frozenset({"columns"}),
    "text": frozenset({"text"}),
    "icon": frozenset({"name"}),
    "badge": frozenset({"label"}),
    "button": frozenset({"label", "actionId", "actionValue"}),
    "segmentedSelection": frozenset({"options", "selected", "actionId", "actionValue"}),
    "expandable": frozenset({"label", "expanded"}),
    "column": frozenset(),
    "row": frozenset(),
    "list": frozenset(),
    "divider": frozenset(),
}

_STRING_PROPERTIES = frozenset(
    {"title", "text", "name", "label", "selected"}
)

# Every segment must start with a letter or an underscore. This rules out the
# `$data.forecast.0.label` form the provider guide shows as an example - the
# guide is wrong and this regex, copied from the client, is right.
_BINDING = re.compile(r"^\$data(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")


def frame(operations: list[dict]) -> str:
    """The portable frame: one open marker, one JSON object per line, one
    close marker. `eve.ui.persist` puts this in the AI message so a reopened
    session still has the surface - `custom` frames are streamed, never
    stored."""
    body = "\n".join(_compact(operation) for operation in operations)
    return f"{OPENING_MARKER}{body}{CLOSING_MARKER}"


def append_frame(content: str | list, operations: list[dict]) -> str | list:
    """The one builder both frame producers share: `eve.ui.persist.persist_ui`
    (a model's own final answer plus this turn's `create` operations) and
    `eve.ui.actions.ui_action` (a one-line reply plus the tap's `patch`). One
    contract rather than two conventions for the same shape, so
    `strip_frames`/`strip_frames_from_content` only ever have to invert what
    THIS function produces.

    Falsy `content` (there is no reply to put ahead of the frame) gets the
    frame back with nothing prepended - the bare shape `_FRAME_SUFFIX`'s
    `\\A` branch below exists to still recognize. Non-empty string content
    gets a leading "\\n" before the frame. List content (reasoning-capable
    models return typed content blocks) always gets a whole new text block
    appended, never merged into an existing one - concatenating a string
    onto a list would corrupt the message.
    """
    text = frame(operations)
    if isinstance(content, list):
        return [*content, {"type": "text", "text": f"\n{text}"}]
    return text if not content else f"{content}\n{text}"


# The prefix a frame appended by `append_frame` can have: "start-of-string"
# when it was appended to falsy content, or a literal "\n" when it was
# appended to something. Matching that literal suffix, rather than the
# markers wherever they occur, is the tolerant-but-conservative reading: a
# message that merely mentions "<assistant-ui>" in passing - with no
# "</assistant-ui>" at the true end of the string - is left untouched.
_NOT_ANOTHER_OPEN = r"(?:(?!" + re.escape(OPENING_MARKER) + r").)*"

# Two things this has to get right, both reproduced against a naive first cut:
#
# 1. The prefix is "start-of-string OR a literal newline", not just a literal
#    newline - see `append_frame` just above for why both shapes are legal.
#    Requiring a leading "\n" would make the bare shape invisible to this
#    function entirely.
# 2. The body between the markers must not be allowed to contain another
#    occurrence of the opening marker. A plain `.*` (greedy or not) will
#    happily span PAST an earlier, unrelated "<assistant-ui>\n" - e.g. text
#    that merely mentions the marker before a real frame later in the same
#    message - and eat everything in between as if it were frame body.
#    `(?!OPENING_MARKER).` repeated is "any character, as long as a frame
#    does not start here": it forces the match to resolve against the LAST
#    opening marker that is followed by a well-formed close at the true end
#    of the string, leaving any earlier lookalike untouched.
_FRAME_SUFFIX = re.compile(
    r"(?:\A|\n)"
    + re.escape(OPENING_MARKER)
    + _NOT_ANOTHER_OPEN
    + re.escape(CLOSING_MARKER)
    + r"\Z",
    re.DOTALL,
)


def strip_frames(text: str) -> str:
    """Undo what `persist_ui` (and `ui_action`) did to an AIMessage's
    content, for every place a persisted message is fed back into a prompt
    rather than shown to the member.

    `persist_ui` (`eve.ui.persist`) exists to make a card survive a session
    reopen, by writing the turn's surface into `messages` - text the client
    strips before it ever reaches the member or TTS. `ui_action`
    (`eve.ui.actions`) writes the same shape for a related but different
    reason: a tap on a rendered surface answers with a one-line reply plus
    the tap's patch, built through the same `append_frame` both producers
    share, rather than a model's own free-form prose. Either way `messages`
    is also what both the VOICE model (`eve.graph`)
    and REFLEX (`eve.memory.extract`, for extraction and for the thread
    digest) read back as conversation history on every later turn. Left
    unstripped, a surface that can be multiple KiB (the protocol's own
    ceiling is 48 KiB) is fed into every subsequent prompt, REFLEX can mint
    memories out of the JSON, and the VOICE model gets a worked example of
    the frame syntax in its own prior output to imitate - a self-composed
    frame in `content` would reach the client having passed through none of
    `validate_operation`, unlike a real one which is gated by `stream.emit`.

    A no-op on the near-totality of turns that carry no frame at all.
    """
    return _FRAME_SUFFIX.sub("", text)


def strip_frames_from_content(content: object) -> object:
    """`strip_frames` operates on a plain string; `AIMessage.content` is
    sometimes instead a list of typed blocks (reasoning-capable models), the
    same split `persist.py._with_frame` has to handle when it APPENDS a
    frame. For the list shape, `_with_frame` always adds a whole new
    `{"type": "text", "text": ...}` block whose text is nothing but the
    frame - so stripping that block's text down to "" means the block itself
    must be dropped, not left behind as an empty one. Any other value
    (a non-list, non-str content, or a block missing a string `text`) passes
    through unchanged."""
    if isinstance(content, str):
        return strip_frames(content)
    if not isinstance(content, list):
        return content
    kept = []
    for block in content:
        text = block.get("text") if isinstance(block, dict) else None
        if isinstance(text, str):
            stripped = strip_frames(text)
            if stripped == "" and text != "":
                continue
            if stripped != text:
                block = {**block, "text": stripped}
        kept.append(block)
    return kept


def validate_operation(operation: object) -> str | None:
    """`None` when `operation` is a legal create/patch/delete, otherwise the
    same structural diagnostic code the client would have logged."""
    if not isinstance(operation, dict):
        return "malformed-frame"
    if operation.get("protocol") != PROTOCOL:
        return "protocol"
    kind = operation.get("op")
    if kind == "create":
        return _validate_create(operation)
    if kind == "patch":
        return _validate_patch(operation)
    if kind == "delete":
        return _surface_id_error(operation.get("surfaceId"))
    return "operation"


def validate_json_value(value: object) -> str | None:
    """The client retains only JSON-safe values, with every string under the
    2,048-character ceiling wherever it appears - inside `data`, inside
    `localState`, inside a component's properties, at any depth."""
    if isinstance(value, str):
        return "string-limit" if len(value) > MAX_STRING else None
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return None
    if isinstance(value, list):
        for item in value:
            error = validate_json_value(item)
            if error:
                return error
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                return "json-value"
            error = validate_json_value(key) or validate_json_value(item)
            if error:
                return error
        return None
    return "json-value"


def _compact(value: object) -> str:
    # No spaces, matching Dart's `jsonEncode`, so a byte count taken here is
    # the byte count the client will take.
    return json.dumps(value, separators=(",", ":"))


def _byte_length(value: object) -> int:
    return len(_compact(value).encode())


def _surface_id_error(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return "surface-id"
    return "string-limit" if len(value) > MAX_STRING else None


def _validate_create(operation: dict) -> str | None:
    surface = operation.get("surface")
    if not isinstance(surface, dict):
        return "surface"
    for key in ("surfaceId", "catalogId", "catalogVersion"):
        value = surface.get(key)
        if not isinstance(value, str) or not value or len(value) > MAX_STRING:
            return "string"
    if surface["catalogVersion"] != CATALOG_VERSION:
        return "catalog-version"
    if surface["catalogId"] not in CATALOG_IDS:
        return "catalog"
    error = _validate_components(surface.get("components", []))
    if error:
        return error
    normalized = {
        "surfaceId": surface["surfaceId"],
        "catalogId": surface["catalogId"],
        "catalogVersion": surface["catalogVersion"],
        "components": surface.get("components", []),
    }
    for key in ("data", "localState"):
        value = surface.get(key, {})
        if not isinstance(value, dict):
            return "json-value"
        error = validate_json_value(value)
        if error:
            return error
        normalized[key] = value
    # Measured on the normalized dict, because the client measures
    # `jsonEncode(surface.toJson())`, which fills in the absent keys.
    if _byte_length(normalized) > MAX_DEFINITION_BYTES:
        return "definition-size-limit"
    return None


def _validate_patch(operation: dict) -> str | None:
    error = _surface_id_error(operation.get("surfaceId"))
    if error:
        return error
    patch = operation.get("patch")
    if not isinstance(patch, dict):
        return "patch"
    components = patch.get("components")
    if components is not None:
        error = _validate_components(components)
        if error:
            return error
    data_patch = patch.get("dataPatch", {})
    if not isinstance(data_patch, dict):
        return "patch"
    error = validate_json_value(data_patch)
    if error:
        return error
    if _byte_length(patch) > MAX_PATCH_BYTES:
        return "patch-size-limit"
    return None


def _validate_components(components: object) -> str | None:
    if not isinstance(components, list):
        return "component-type"
    seen = 0

    def visit(component: object, depth: int) -> str | None:
        nonlocal seen
        seen += 1
        if seen > MAX_COMPONENTS:
            return "component-limit"
        if depth > MAX_DEPTH:
            return "depth-limit"
        if not isinstance(component, dict):
            return "component-type"
        for key in ("id", "type"):
            value = component.get(key)
            if not isinstance(value, str) or not value or len(value) > MAX_STRING:
                return "string"
        if component["type"] not in CATALOG_IDS:
            return "component-type"
        error = _validate_properties(component["type"], component.get("properties", {}))
        if error:
            return error
        children = component.get("children", [])
        if not isinstance(children, list):
            return "component-type"
        for child in children:
            error = visit(child, depth + 1)
            if error:
                return error
        return None

    for component in components:
        error = visit(component, 1)
        if error:
            return error
    return None


def _validate_properties(component_type: str, properties: object) -> str | None:
    if not isinstance(properties, dict):
        return "component-schema"
    allowed = _ALLOWED_PROPERTIES.get(component_type, frozenset())
    for key, value in properties.items():
        if key not in allowed:
            return "component-schema"
        error = _validate_property(key, value)
        if error:
            return error
    return None


def _validate_property(key: str, value: object) -> str | None:
    if key in _STRING_PROPERTIES:
        return _string_or_binding(value)
    if key == "columns":
        legal = isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 6
        return None if legal else "component-schema"
    if key == "options":
        if not isinstance(value, list):
            return "component-schema"
        for option in value:
            error = _string_or_binding(option)
            if error:
                return error
        return None
    if key == "expanded":
        return None if isinstance(value, bool) else "component-schema"
    if key == "actionId":
        return None if value in ACTION_IDS else "action-schema"
    if key == "actionValue":
        if isinstance(value, str):
            return _string_or_binding(value)
        legal = value is None or isinstance(value, (bool, int, float))
        return None if legal else "action-schema"
    return "component-schema"


def _string_or_binding(value: object) -> str | None:
    if not isinstance(value, str):
        return "component-schema"
    if value.startswith("$") and not _BINDING.match(value):
        return "binding"
    return validate_json_value(value)
