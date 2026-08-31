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
emission, `eve.ui.weather` owns the one surface V1 ships.
"""

from __future__ import annotations

import json
import re

PROTOCOL = "assistant-ui/1.0"
CATALOG_VERSION = "1"

# The closed V1 catalog. The same thirteen ids are legal as a surface's
# `catalogId` AND as a component's `type` - the client checks both against one
# set (`DynamicSurfaceProtocol._componentTypes`), so this file does too.
CATALOG_IDS = frozenset(
    {
        "weather",
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

# V1 has exactly one interactive contract. A provider cannot invent an action.
ACTION_IDS = frozenset({"weather.rangeChanged"})

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
    "weather": frozenset({"location", "condition", "temperature"}),
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
    {"location", "condition", "title", "text", "name", "label", "selected"}
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


# `persist.py._with_frame` only ever appends this exact shape - a leading
# "\n", then the markers `frame` returns, anchored at the very end of the
# content it was appended to. Matching that literal suffix, rather than the
# markers wherever they occur, is the tolerant-but-conservative reading: a
# message that merely mentions "<assistant-ui>" in passing - with no
# "</assistant-ui>" at the true end of the string - is left untouched.
_FRAME_SUFFIX = re.compile(
    r"\n" + re.escape(OPENING_MARKER) + r".*" + re.escape(CLOSING_MARKER) + r"$",
    re.DOTALL,
)


def strip_frames(text: str) -> str:
    """Undo what `persist_ui` did to an AIMessage's content, for every place
    a persisted message is fed back into a prompt rather than shown to the
    member.

    `persist_ui` (`eve.ui.persist`) exists to make a card survive a session
    reopen, by writing the turn's surface into `messages` - text the client
    strips before it ever reaches the member or TTS. But `messages` is also
    what both the VOICE model (`eve.graph`) and REFLEX
    (`eve.memory.extract`, for extraction and for the thread digest) read
    back as conversation history on every later turn. Left unstripped, a
    surface that can be multiple KiB (the protocol's own ceiling is 48 KiB)
    is fed into every subsequent prompt, REFLEX can mint memories out of the
    JSON, and the VOICE model gets a worked example of the frame syntax in
    its own prior output to imitate - a self-composed frame in `content`
    would reach the client having passed through none of
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
    if key == "temperature":
        return _number_or_binding(value)
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


def _number_or_binding(value: object) -> str | None:
    if isinstance(value, bool):
        return "component-schema"
    if isinstance(value, (int, float)):
        return None
    if isinstance(value, str):
        if _BINDING.match(value):
            return None
        return "binding" if value.startswith("$") else "component-schema"
    return "component-schema"
