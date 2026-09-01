# Dynamic chat UI (LangGraph side) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Eve's LangGraph server drive the Flutter client's `assistant-ui/1.0` dynamic surface protocol — Eve puts a live weather card on screen over the `custom` stream mode, the card survives a session reopen, and tapping "7-day" round-trips back through the graph for the missing forecast.

**Architecture:** A new `src/eve/ui/` package owns the wire contract. Surfaces are built **server-side from Home Assistant's own forecast** and merely *triggered* by the model calling a no-argument `show_weather` tool — the model never authors surface JSON. Live rendering goes out over LangGraph's `custom` stream mode (`{"assistant_ui": <operation>}`); a `persist_ui` node additionally copies the same operation into the final AI message as a portable `<assistant-ui>` frame, because `custom` frames are streamed and never stored, and the client replays a reopened session from `GET /threads/{id}/state` messages only. An inbound tap arrives as the next turn's user text (an `<assistant-ui-action>` envelope), so `load_context` routes it to a model-free `ui_action` node that re-fetches the forecast and emits one patch.

**Tech Stack:** Python 3.12, LangGraph 1.2.11, LangChain, Aegra (aegra-api 0.10.3), FastAPI (eve-tools), httpx, pytest + pytest-asyncio (`asyncio_mode = "auto"` — async tests need no decorator).

**Spec:**
- Linear EVE-12 — <https://linear.app/chalifour-development/issue/EVE-12/implement-langgraph-side-support-for-the-dynamic-chat-ui-protocol>
- `docs/internals/dynamic-chat-ui.md` in the **flutter-open-assistant** repo (`~/GitHub/open-assistant/flutter-open-assistant`) — the provider integration guide. The client is the source of truth; every contract this plan pins was read out of that repo's Dart source, cited inline per task.

---

## Global Constraints

Copy these values verbatim. Every task's requirements implicitly include this section.

- **Protocol string:** exactly `assistant-ui/1.0`. **Catalog version:** exactly `"1"` (a string, not an int).
- **Closed V1 catalog** — the only legal `catalogId` *and* component `type` values, all thirteen: `weather`, `column`, `row`, `card`, `list`, `grid`, `divider`, `text`, `icon`, `badge`, `button`, `segmentedSelection`, `expandable`.
- **Per-type allowed properties** (anything else → reject): `weather`: `location`, `condition`, `temperature`. `card`: `title`. `grid`: `columns` (int 1–6). `text`: `text`. `icon`: `name`. `badge`: `label`. `button`: `label`, `actionId`, `actionValue`. `segmentedSelection`: `options`, `selected`, `actionId`, `actionValue`. `expandable`: `label`, `expanded` (bool). `column`, `row`, `list`, `divider`: none.
- **`actionId` is restricted to exactly `weather.rangeChanged`.** V1 has one interactive contract. Anything else is an `action-schema` rejection.
- **`$data.` bindings:** a string property may be a literal or a binding matching exactly `^\$data(?:\.[A-Za-z_][A-Za-z0-9_]*)+$`. Note: the provider guide's example `$data.forecast.0.label` does **not** match that regex (segments must start with a letter or underscore) — the regex in `dynamic_surface_protocol.dart` is authoritative, so numeric path segments are illegal. Do not use them.
- **Limits:** 8 surfaces per turn; 64 components per surface; component tree depth 8; 2,048 characters per string value; 48 KiB serialized surface definition JSON; 16 KiB serialized patch JSON; 30 accepted updates per `surfaceId` per rolling 60 seconds.
- **Capabilities arrive at `config["configurable"]["assistant_ui"]`, NOT in run metadata.** EVE-12's description says `metadata.assistant_ui`; that is true of the OpenClaw gateway path and false of LangGraph. `langgraph_agent_service.dart:318-321` sends `'config': {'configurable': {'assistant_ui': capabilities.toJson()}}` with a comment explaining why: LangGraph indexes run metadata and rejects a non-scalar value there. Aegra passes request `config.configurable` through to the graph verbatim (`aegra_api/services/run_preparation.py:222-240`).
- **Fail closed on capabilities.** No declaration, wrong protocol, wrong catalog version, or catalog id absent ⇒ emit nothing and answer in prose.
- **Privacy-safe logging.** Every log line in this feature carries structural diagnostics only: the operation name, the catalog id/version, the specific limit that was hit. Never member text, never surface `data`, never a full payload.
- **Serialize JSON compactly** — `json.dumps(value, separators=(",", ":"))` — so byte-limit checks match the client's `jsonEncode`, which emits no spaces.

---

### Task 1: The wire contract and its validator

`src/eve/ui/protocol.py` is a pure module: constants, one validator, one framer. No LangGraph, no I/O, no Eve state. Everything downstream depends on it.

Why a server-side copy of a client-side validator: the client rejects **silently**. A surface that fails validation renders one neutral "This content can't be shown" card, and on the native (`custom`) path is dropped with a log line that stays on the phone. Validating before we emit turns an invisible client-side drop into a server-side log we can actually see, and it is the only place a bad surface template gets caught at all.

**Files:**
- Create: `src/eve/ui/__init__.py` (empty)
- Create: `src/eve/ui/protocol.py`
- Test: `tests/test_ui_protocol.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `PROTOCOL: str`, `CATALOG_VERSION: str`, `CATALOG_IDS: frozenset[str]`, `ACTION_IDS: frozenset[str]`
  - `MAX_SURFACES_PER_TURN: int`, `MAX_COMPONENTS: int`, `MAX_DEPTH: int`, `MAX_STRING: int`, `MAX_DEFINITION_BYTES: int`, `MAX_PATCH_BYTES: int`, `MAX_UPDATES_PER_MINUTE: int`
  - `validate_operation(operation: object) -> str | None` — a diagnostic code, or `None` when the operation is legal
  - `validate_json_value(value: object) -> str | None`
  - `frame(operations: list[dict]) -> str` — the portable `<assistant-ui>` block

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui_protocol.py`:

```python
"""The server side of `assistant-ui/1.0`, tested against the shapes the
client's own Dart validators accept and reject
(flutter-open-assistant, lib/data/services/agent/dynamic_surface_protocol.dart
and lib/domain/models/dynamic_ui/dynamic_surface.dart).
"""

from __future__ import annotations

import json

from eve.ui import protocol


def _surface(**overrides) -> dict:
    surface = {
        "surfaceId": "wx-1",
        "catalogId": "weather",
        "catalogVersion": "1",
        "components": [
            {
                "id": "weather",
                "type": "weather",
                "properties": {
                    "location": "$data.location",
                    "condition": "$data.condition",
                    "temperature": "$data.temperature",
                },
                "children": [],
            }
        ],
        "data": {"location": "Home", "condition": "Sunny", "temperature": 21},
        "localState": {},
    }
    surface.update(overrides)
    return {"protocol": protocol.PROTOCOL, "op": "create", "surface": surface}


def test_a_well_formed_weather_create_is_accepted():
    assert protocol.validate_operation(_surface()) is None


def test_the_protocol_string_must_match_exactly():
    operation = _surface()
    operation["protocol"] = "assistant-ui/1.1"
    assert protocol.validate_operation(operation) == "protocol"


def test_an_unknown_operation_is_rejected():
    operation = _surface()
    operation["op"] = "replace"
    assert protocol.validate_operation(operation) == "operation"


def test_a_catalog_id_outside_the_closed_v1_set_is_rejected():
    assert protocol.validate_operation(_surface(catalogId="thermostat")) == "catalog"


def test_a_catalog_version_other_than_1_is_rejected():
    assert (
        protocol.validate_operation(_surface(catalogVersion="2")) == "catalog-version"
    )


def test_a_component_type_outside_the_closed_v1_set_is_rejected():
    components = [{"id": "x", "type": "thermostat", "properties": {}, "children": []}]
    assert protocol.validate_operation(_surface(components=components)) == "component-type"


def test_a_property_the_component_type_does_not_declare_is_rejected():
    components = [
        {"id": "t", "type": "text", "properties": {"label": "hi"}, "children": []}
    ]
    assert (
        protocol.validate_operation(_surface(components=components))
        == "component-schema"
    )


def test_a_malformed_data_binding_is_rejected_as_a_binding_error():
    """A `$`-prefixed string that is not a legal binding is a `binding`
    error, not a generic schema error - the client distinguishes them and so
    must the diagnostic we log."""
    components = [
        {"id": "t", "type": "text", "properties": {"text": "$data"}, "children": []}
    ]
    assert protocol.validate_operation(_surface(components=components)) == "binding"


def test_a_numeric_path_segment_is_not_a_legal_binding():
    """The provider guide's `$data.forecast.0.label` example contradicts the
    client regex, which requires every segment to start with a letter or an
    underscore. The regex wins."""
    components = [
        {
            "id": "t",
            "type": "text",
            "properties": {"text": "$data.forecast.0.label"},
            "children": [],
        }
    ]
    assert protocol.validate_operation(_surface(components=components)) == "binding"


def test_grid_columns_must_be_an_int_between_one_and_six():
    def grid(columns):
        return [{"id": "g", "type": "grid", "properties": {"columns": columns}, "children": []}]

    assert protocol.validate_operation(_surface(components=grid(3))) is None
    assert protocol.validate_operation(_surface(components=grid(7))) == "component-schema"
    assert protocol.validate_operation(_surface(components=grid(True))) == "component-schema"


def test_only_weather_rangechanged_is_a_legal_action_id():
    def button(action_id):
        return [
            {
                "id": "b",
                "type": "button",
                "properties": {"label": "Go", "actionId": action_id},
                "children": [],
            }
        ]

    assert protocol.validate_operation(_surface(components=button("weather.rangeChanged"))) is None
    assert protocol.validate_operation(_surface(components=button("lights.toggle"))) == "action-schema"


def test_more_than_sixty_four_components_is_rejected():
    components = [
        {"id": f"t{index}", "type": "text", "properties": {}, "children": []}
        for index in range(65)
    ]
    assert protocol.validate_operation(_surface(components=components)) == "component-limit"


def test_a_tree_deeper_than_eight_is_rejected():
    node = {"id": "leaf", "type": "text", "properties": {}, "children": []}
    for index in range(8):
        node = {"id": f"c{index}", "type": "column", "properties": {}, "children": [node]}
    assert protocol.validate_operation(_surface(components=[node])) == "depth-limit"


def test_a_string_longer_than_the_limit_is_rejected():
    data = {"location": "x" * (protocol.MAX_STRING + 1)}
    assert protocol.validate_operation(_surface(data=data)) == "string-limit"


def test_a_definition_over_forty_eight_kibibytes_is_rejected():
    data = {"blob": ["x" * 2000 for _ in range(30)]}
    assert protocol.validate_operation(_surface(data=data)) == "definition-size-limit"


def test_a_well_formed_patch_is_accepted():
    operation = {
        "protocol": protocol.PROTOCOL,
        "op": "patch",
        "surfaceId": "wx-1",
        "patch": {"dataPatch": {"selectedRange": "daily", "daily": []}},
    }
    assert protocol.validate_operation(operation) is None


def test_a_patch_without_a_surface_id_is_rejected():
    operation = {
        "protocol": protocol.PROTOCOL,
        "op": "patch",
        "patch": {"dataPatch": {}},
    }
    assert protocol.validate_operation(operation) == "surface-id"


def test_a_patch_over_sixteen_kibibytes_is_rejected():
    operation = {
        "protocol": protocol.PROTOCOL,
        "op": "patch",
        "surfaceId": "wx-1",
        "patch": {"dataPatch": {"blob": ["x" * 2000 for _ in range(10)]}},
    }
    assert protocol.validate_operation(operation) == "patch-size-limit"


def test_a_delete_needs_only_a_surface_id():
    assert (
        protocol.validate_operation(
            {"protocol": protocol.PROTOCOL, "op": "delete", "surfaceId": "wx-1"}
        )
        is None
    )


def test_anything_that_is_not_an_object_is_a_malformed_frame():
    assert protocol.validate_operation("wx-1") == "malformed-frame"


def test_frame_uses_the_exact_markers_the_client_parser_matches():
    """`FramedDynamicSurfaceParser` matches the literal markers
    `'<assistant-ui>\\n'` and `'\\n</assistant-ui>'`, one JSON object per
    line in between. An extra newline before the close breaks the match."""
    first = {"protocol": protocol.PROTOCOL, "op": "delete", "surfaceId": "a"}
    second = {"protocol": protocol.PROTOCOL, "op": "delete", "surfaceId": "b"}
    text = protocol.frame([first, second])

    assert text.startswith("<assistant-ui>\n")
    assert text.endswith("\n</assistant-ui>")
    body = text[len("<assistant-ui>\n") : -len("\n</assistant-ui>")]
    assert [json.loads(line) for line in body.split("\n")] == [first, second]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ui_protocol.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve.ui'`

- [ ] **Step 3: Write the implementation**

Create `src/eve/ui/__init__.py` as an empty file. Create `src/eve/ui/protocol.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_ui_protocol.py -q`
Expected: PASS, 20 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve/ui/__init__.py src/eve/ui/protocol.py tests/test_ui_protocol.py
git commit -m "feat(ui): add the assistant-ui/1.0 wire contract and validator"
```

---

### Task 2: Capability handshake and `custom`-stream emission

`src/eve/ui/stream.py` is the only module in the feature that touches LangGraph's runtime. Two functions: read what the client declared, and write one validated operation to the `custom` stream.

Verified behaviour this rests on (checked against the installed packages, not assumed):
- `config["configurable"]["assistant_ui"]` arrives verbatim from the client through Aegra.
- `langgraph.config.get_stream_writer()` works inside a tool executed by `ToolNode`, and `graph.astream(..., stream_mode="custom")` yields `{"assistant_ui": {...}}` unchanged.
- `get_stream_writer()` raises `RuntimeError` when called outside a runnable context, which is why `emit` wraps it.

**Files:**
- Create: `src/eve/ui/stream.py`
- Test: `tests/test_ui_stream.py`

**Interfaces:**
- Consumes: `eve.ui.protocol.PROTOCOL`, `CATALOG_VERSION`, `validate_operation` (Task 1).
- Produces:
  - `capabilities(config: RunnableConfig | None) -> dict | None`
  - `supports(config: RunnableConfig | None, catalog_id: str) -> bool`
  - `emit(operation: dict) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui_stream.py`:

```python
"""The capability handshake in, and `custom`-mode frames out."""

from __future__ import annotations

from eve.ui import protocol, stream

CAPABILITIES = {
    "protocol": "assistant-ui/1.0",
    "catalogVersion": "1",
    "catalogIds": ["weather", "text", "card"],
}


def _config(**overrides) -> dict:
    declared = {**CAPABILITIES, **overrides}
    return {"configurable": {"assistant_ui": declared}}


def _create(surface_id: str = "wx-1") -> dict:
    return {
        "protocol": protocol.PROTOCOL,
        "op": "create",
        "surface": {
            "surfaceId": surface_id,
            "catalogId": "weather",
            "catalogVersion": "1",
            "components": [],
            "data": {},
            "localState": {},
        },
    }


def test_capabilities_are_read_from_configurable_not_metadata():
    """LangGraph indexes run metadata and rejects a non-scalar value there,
    so the client sends the declaration under `config.configurable`
    (langgraph_agent_service.dart:318-321). Reading `metadata` would find
    nothing, forever, with no error to say so."""
    assert stream.capabilities(_config()) == CAPABILITIES
    assert stream.capabilities({"metadata": {"assistant_ui": CAPABILITIES}}) is None


def test_capabilities_tolerate_a_config_that_declares_nothing():
    assert stream.capabilities(None) is None
    assert stream.capabilities({}) is None
    assert stream.capabilities({"configurable": {}}) is None
    assert stream.capabilities({"configurable": {"assistant_ui": "yes"}}) is None


def test_supports_is_true_only_for_a_declared_catalog_id():
    assert stream.supports(_config(), "weather") is True
    assert stream.supports(_config(), "segmentedSelection") is False


def test_supports_fails_closed():
    """A client that declared nothing cannot render anything. Emitting at it
    would put an unreadable frame in the transcript forever, since history is
    replayed from the AI message text."""
    assert stream.supports(None, "weather") is False
    assert stream.supports(_config(protocol="assistant-ui/2.0"), "weather") is False
    assert stream.supports(_config(catalogVersion="2"), "weather") is False
    assert stream.supports(_config(catalogIds="weather"), "weather") is False


def test_emit_writes_the_operation_under_the_assistant_ui_key(monkeypatch):
    written = []
    monkeypatch.setattr(stream, "get_stream_writer", lambda: written.append)

    assert stream.emit(_create()) is True
    assert written == [{"assistant_ui": _create()}]


def test_emit_refuses_an_operation_the_client_would_reject(monkeypatch):
    written = []
    monkeypatch.setattr(stream, "get_stream_writer", lambda: written.append)
    operation = _create()
    operation["surface"]["catalogId"] = "thermostat"

    assert stream.emit(operation) is False
    assert written == []


def test_emit_returns_false_rather_than_raising_outside_a_run():
    """`get_stream_writer()` raises outside a runnable context. Every caller
    is a tool or a node whose failure must degrade to ordinary Eve prose -
    the same posture as eve.tools_client.invoke."""
    assert stream.emit(_create()) is False


def test_emit_logs_only_structural_diagnostics(caplog):
    """Privacy-safe logging: the diagnostic code and the operation name, never
    `data`, never member text."""
    operation = _create()
    operation["surface"]["data"] = {"location": "17 Privacy Lane"}
    operation["surface"]["catalogId"] = "thermostat"

    with caplog.at_level("WARNING"):
        stream.emit(operation)

    assert "catalog" in caplog.text
    assert "Privacy Lane" not in caplog.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ui_stream.py -q`
Expected: FAIL — `ImportError: cannot import name 'stream' from 'eve.ui'`

- [ ] **Step 3: Write the implementation**

Create `src/eve/ui/stream.py`:

```python
"""The capability handshake in, `custom`-mode frames out. The only module in
this package that touches LangGraph's runtime.
"""

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from eve.ui import protocol

logger = logging.getLogger(__name__)


def capabilities(config: RunnableConfig | None) -> dict | None:
    """The client's `DynamicUiCapabilities.toJson()`, or None.

    `config.configurable`, NOT run metadata. LangGraph indexes run metadata
    and rejects anything but a scalar there ("metadata value for key
    'assistant_ui' must be str/int/float/bool, got dict"), so the client
    sends the object under `config.configurable.assistant_ui` instead -
    `langgraph_agent_service.dart:318-321` carries the same comment. Aegra
    merges request config into the graph config verbatim
    (`aegra_api/services/run_preparation.py`), so it arrives here unchanged.
    EVE-12's own description says `metadata.assistant_ui`; that is the
    OpenClaw gateway's shape, not LangGraph's.
    """
    configurable = (config or {}).get("configurable") or {}
    declared = configurable.get("assistant_ui")
    return declared if isinstance(declared, dict) else None


def supports(config: RunnableConfig | None, catalog_id: str) -> bool:
    """Fails CLOSED.

    A client that declared nothing cannot render anything, and a surface is
    not free to emit at it: `eve.ui.persist` writes the same operation into
    the AI message so the card survives a reopen, which would leave an
    unreadable frame in that thread's transcript permanently. Silence is the
    correct answer, and Eve still has words.
    """
    declared = capabilities(config)
    if declared is None:
        return False
    if declared.get("protocol") != protocol.PROTOCOL:
        return False
    if declared.get("catalogVersion") != protocol.CATALOG_VERSION:
        return False
    ids = declared.get("catalogIds")
    return isinstance(ids, list) and catalog_id in ids


def emit(operation: dict) -> bool:
    """Validate, then write one operation to the `custom` stream.

    `{"assistant_ui": <one operation>}` is the exact envelope
    `LangGraphAgentService._handleCustom` unwraps; anything else on the
    `custom` channel is ignored by the client rather than erroring.

    Returns False and never raises. Every caller is a tool or a node whose
    failure has to degrade to ordinary Eve prose - the same posture as
    `eve.tools_client.invoke` - and `get_stream_writer()` itself raises
    `RuntimeError` outside a runnable context.
    """
    error = protocol.validate_operation(operation)
    if error is not None:
        # Structural diagnostics only. The client's own logging holds the
        # same line for the same reason.
        logger.warning(
            "assistant_ui operation rejected: %s (op=%r)",
            error,
            operation.get("op") if isinstance(operation, dict) else None,
        )
        return False
    try:
        get_stream_writer()({"assistant_ui": operation})
    except Exception:
        logger.warning("assistant_ui write failed (op=%r)", operation.get("op"))
        return False
    return True
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_ui_stream.py -q`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve/ui/stream.py tests/test_ui_stream.py
git commit -m "feat(ui): read client capabilities and emit custom-stream frames"
```

---

### Task 3: The weather forecast relay in eve-tools

The card's contents come from Home Assistant, not from the model. Eve has exactly one weather source and this is it. `eve-tools` stays a thin HTTP relay (ADR 0006, `docs/architecture.md` "Specialists and skills"): it returns HA's raw shape and does no presentation.

Two things force a new function rather than reuse of `call_service`:
1. `weather.get_forecasts` is a **response service**. Without `?return_response` HA answers `200` with an empty body and the forecast is simply lost. `home_assistant.call_service` does not send that query parameter, and adding it there would change every existing caller.
2. The card needs current conditions *and* both forecast ranges. Three HA calls behind one relay call keeps the round trips off Eve's side of the boundary.

**Files:**
- Modify: `src/eve_tools/home_assistant.py` (append `weather`)
- Modify: `src/eve_tools/app.py:27-49` (add one `_HANDLERS` entry)
- Modify: `tests/fixtures/stub_home_assistant.py`
- Test: `tests/test_eve_tools_home_assistant.py` (append), `tests/test_eve_tools_app.py` (append)

**Interfaces:**
- Consumes: `eve_tools.settings.get_tools_settings`.
- Produces:
  - `eve_tools.home_assistant.weather(entity_id: str | None = None) -> dict` returning
    `{"entity_id": str, "location": str, "condition": str, "temperature": float | int | None, "hourly": list[dict], "daily": list[dict]}`
    where each forecast entry is HA's raw dict (`datetime`, `condition`, `temperature`, …).
  - The `home.weather` tool name, reachable through `eve.tools_client.invoke("home.weather", {})`.

- [ ] **Step 1: Extend the stub Home Assistant with a weather entity**

Edit `tests/fixtures/stub_home_assistant.py`. Add the weather entity and its forecast service, and make `get_state` return attributes. The `/api/services/weather/get_forecasts` route **must be declared before** the generic `/api/services/{domain}/{service}` route — FastAPI matches in declaration order, and the generic handler would otherwise treat the forecast request as a state change.

Replace the whole file with:

```python
"""A minimal stand-in for Home Assistant's REST API, for integration tests
that exercise the real HTTP boundary to eve-tools without touching the real
home lab instance.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI()
# More than one light so a "how many lights are on" request has a real
# answer to count, and enough of them that answering needs several rounds of
# get_state - the shape that surfaced EVE-15.
_states = {
    "light.kitchen": "off",
    "light.living_room": "on",
    "light.bedroom": "on",
    "light.porch": "off",
    "light.garage": "on",
    "light.office": "off",
}

# A weather entity with the attribute blob HA actually returns for one:
# `state` is the condition slug, `temperature` lives in `attributes`.
_WEATHER_ENTITY = "weather.home"
_WEATHER_STATE = "partlycloudy"
_WEATHER_ATTRIBUTES = {
    "friendly_name": "Home",
    "temperature": 21.4,
    "temperature_unit": "°C",
    "humidity": 62,
}

_FORECASTS = {
    "hourly": [
        {
            "datetime": f"2026-08-31T{hour:02d}:00:00+00:00",
            "condition": "partlycloudy" if hour % 2 else "sunny",
            "temperature": 18 + hour % 7,
        }
        for hour in range(12)
    ],
    "daily": [
        {
            "datetime": f"2026-09-0{day}T12:00:00+00:00",
            "condition": "rainy" if day % 2 else "cloudy",
            "temperature": 20 + day,
            "templow": 12 + day,
        }
        for day in range(1, 8)
    ],
}


@app.get("/api/states")
async def list_states() -> list:
    """HA returns every entity, not just the lights, and each one carries an
    `attributes` blob - both of which `home_assistant.list_entities` has to
    filter and trim, so the stub has to produce them."""
    entities = [
        {
            "entity_id": entity_id,
            "state": state,
            "attributes": {"friendly_name": entity_id.split(".")[1].replace("_", " ")},
        }
        for entity_id, state in {**_states, "sensor.outside_temp": "11.4"}.items()
    ]
    entities.append(
        {
            "entity_id": _WEATHER_ENTITY,
            "state": _WEATHER_STATE,
            "attributes": dict(_WEATHER_ATTRIBUTES),
        }
    )
    return entities


@app.get("/api/states/{entity_id}")
async def get_state(entity_id: str) -> dict:
    if entity_id == _WEATHER_ENTITY:
        return {
            "entity_id": entity_id,
            "state": _WEATHER_STATE,
            "attributes": dict(_WEATHER_ATTRIBUTES),
        }
    return {"entity_id": entity_id, "state": _states.get(entity_id, "unknown")}


# Declared BEFORE the generic service route below: FastAPI matches in
# declaration order, and `call_service` would otherwise swallow this as a
# state change and answer `[]`.
@app.post("/api/services/weather/get_forecasts")
async def get_forecasts(body: dict, return_response: str | None = None) -> dict:
    """HA drops the forecast entirely unless `?return_response` is present -
    the exact failure this stub has to be able to reproduce."""
    if return_response is None:
        return {"changed_states": []}
    forecast = _FORECASTS.get(body.get("type"), [])
    return {
        "changed_states": [],
        "service_response": {body["entity_id"]: {"forecast": forecast}},
    }


@app.post("/api/services/{domain}/{service}")
async def call_service(domain: str, service: str, body: dict) -> list:
    _states[body["entity_id"]] = "on" if service == "turn_on" else "off"
    return []
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_eve_tools_home_assistant.py`. That file already has an autouse `_settings` fixture pointing `EVE_TOOLS_HOME_ASSISTANT_URL` at `http://ha.test`, and uses the `@respx.mock` decorator with `respx.get(...).mock(return_value=httpx.Response(...))`. Match it — no new imports are needed:

```python
@respx.mock
async def test_weather_returns_current_conditions_and_both_ranges():
    respx.get("http://ha.test/api/states/weather.home").mock(
        return_value=httpx.Response(
            200,
            json={
                "entity_id": "weather.home",
                "state": "partlycloudy",
                "attributes": {"friendly_name": "Home", "temperature": 21.4},
            },
        )
    )
    respx.post("http://ha.test/api/services/weather/get_forecasts").mock(
        return_value=httpx.Response(
            200,
            json={
                "service_response": {
                    "weather.home": {
                        "forecast": [
                            {
                                "datetime": "2026-08-31T14:00:00+00:00",
                                "condition": "sunny",
                                "temperature": 22,
                            }
                        ]
                    }
                }
            },
        )
    )

    result = await home_assistant.weather("weather.home")

    assert result["entity_id"] == "weather.home"
    assert result["location"] == "Home"
    assert result["condition"] == "partlycloudy"
    assert result["temperature"] == 21.4
    # Both ranges come back, from one relay call: the card needs the hourly
    # strip now and `ui_action` needs the daily one on the next turn.
    assert result["hourly"][0]["temperature"] == 22
    assert result["daily"][0]["temperature"] == 22


@respx.mock
async def test_weather_asks_for_the_response_body():
    """Without `?return_response` HA answers 200 with an empty body and the
    forecast is silently lost. This assertion is the whole reason this
    function exists instead of a `call_service` call."""
    respx.get("http://ha.test/api/states/weather.home").mock(
        return_value=httpx.Response(
            200, json={"state": "sunny", "attributes": {"temperature": 20}}
        )
    )
    route = respx.post("http://ha.test/api/services/weather/get_forecasts").mock(
        return_value=httpx.Response(200, json={"service_response": {}})
    )

    await home_assistant.weather("weather.home")

    assert "return_response" in str(route.calls[0].request.url)


@respx.mock
async def test_weather_discovers_the_entity_when_none_is_named():
    respx.get("http://ha.test/api/states").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"entity_id": "light.kitchen", "state": "on", "attributes": {}},
                {"entity_id": "weather.cottage", "state": "rainy", "attributes": {}},
            ],
        )
    )
    respx.get("http://ha.test/api/states/weather.cottage").mock(
        return_value=httpx.Response(
            200, json={"state": "rainy", "attributes": {"temperature": 12}}
        )
    )
    respx.post("http://ha.test/api/services/weather/get_forecasts").mock(
        return_value=httpx.Response(200, json={"service_response": {}})
    )

    result = await home_assistant.weather()

    assert result["entity_id"] == "weather.cottage"


@respx.mock
async def test_weather_survives_a_range_the_entity_does_not_publish():
    """Plenty of HA weather integrations have no hourly forecast. That is an
    empty range, not a failed call - the card still renders, and the client
    dispatches a remote action for whichever range is absent."""
    respx.get("http://ha.test/api/states/weather.home").mock(
        return_value=httpx.Response(
            200, json={"state": "sunny", "attributes": {"temperature": 20}}
        )
    )
    respx.post("http://ha.test/api/services/weather/get_forecasts").mock(
        return_value=httpx.Response(500, json={"message": "not supported"})
    )

    result = await home_assistant.weather("weather.home")

    assert result["hourly"] == []
    assert result["daily"] == []


@respx.mock
async def test_weather_raises_when_the_home_has_no_weather_entity():
    """`eve_tools.app.invoke_tool` turns a raised exception into
    `{"error": ...}` with a 200, which `eve.tools_client.invoke` hands back as
    an `error: ...` string. Raising is how this reaches the caller."""
    respx.get("http://ha.test/api/states").mock(
        return_value=httpx.Response(200, json=[])
    )

    with pytest.raises(ValueError):
        await home_assistant.weather()
```

Append to `tests/test_eve_tools_app.py`:

```python
def test_home_weather_is_a_dispatchable_tool():
    """The dispatch table is the whole routing layer - a handler that exists
    but is unregistered 404s at runtime with nothing failing at import."""
    from eve_tools.app import _HANDLERS

    assert "home.weather" in _HANDLERS
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_eve_tools_home_assistant.py tests/test_eve_tools_app.py -q`
Expected: FAIL — `AttributeError: module 'eve_tools.home_assistant' has no attribute 'weather'`, and the dispatch assertion fails.

- [ ] **Step 4: Write the implementation**

Append to `src/eve_tools/home_assistant.py`:

```python
# ponytail: a flat trim, not a window the caller chooses. HA hands back 48
# hourly entries and the card lays out a handful of cells; `eve.ui.weather`
# trims again for presentation. Raise it if a range ever needs more.
_MAX_FORECAST_ENTRIES = 24


async def weather(entity_id: str | None = None) -> dict:
    """Current conditions plus the hourly AND daily forecast for one HA
    weather entity - everything the `weather` surface needs, in one relay call.

    Not `call_service`: `weather.get_forecasts` is a *response* service, and
    without `?return_response` HA answers 200 with an empty body and the
    forecast is silently lost. Adding that parameter to `call_service` would
    change every existing caller.

    Raises when the home has no weather entity at all. `eve_tools.app`'s
    `/invoke` turns that into `{"error": ...}` with a 200, which is the shape
    `eve.tools_client.invoke` already degrades to a returned string.
    """
    settings = get_tools_settings()
    base = settings.home_assistant_url
    headers = {"Authorization": f"Bearer {settings.home_assistant_token}"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        if entity_id is None:
            listing = await client.get(f"{base}/api/states", headers=headers)
            listing.raise_for_status()
            entity_id = next(
                (
                    entity["entity_id"]
                    for entity in listing.json()
                    if entity["entity_id"].startswith("weather.")
                ),
                None,
            )
            if entity_id is None:
                raise ValueError("no weather entity in Home Assistant")

        current = await client.get(f"{base}/api/states/{entity_id}", headers=headers)
        current.raise_for_status()
        state = current.json()

        forecasts: dict[str, list] = {}
        for kind in ("hourly", "daily"):
            response = await client.post(
                f"{base}/api/services/weather/get_forecasts",
                params={"return_response": ""},
                headers=headers,
                json={"entity_id": entity_id, "type": kind},
            )
            # A range the entity does not publish (plenty of HA weather
            # integrations have no hourly forecast) is an empty range, not a
            # failed call: the card still renders, and the client dispatches a
            # remote action for whichever range is absent.
            if response.status_code >= 400:
                forecasts[kind] = []
                continue
            body = response.json()
            entries = (
                body.get("service_response", {}).get(entity_id, {}).get("forecast", [])
            )
            forecasts[kind] = entries[:_MAX_FORECAST_ENTRIES]

    attributes = state.get("attributes", {})
    return {
        "entity_id": entity_id,
        "location": attributes.get("friendly_name", ""),
        "condition": state.get("state", ""),
        "temperature": attributes.get("temperature"),
        "hourly": forecasts["hourly"],
        "daily": forecasts["daily"],
    }
```

Add one entry to `_HANDLERS` in `src/eve_tools/app.py`, directly after `"home.call_service"`:

```python
    "home.weather": lambda a: home_assistant.weather(a.get("entity_id")),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_eve_tools_home_assistant.py tests/test_eve_tools_app.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/eve_tools/home_assistant.py src/eve_tools/app.py tests/fixtures/stub_home_assistant.py tests/test_eve_tools_home_assistant.py tests/test_eve_tools_app.py
git commit -m "feat(tools): relay Home Assistant weather forecasts"
```

---

### Task 4: The weather surface, built server-side

`src/eve/ui/weather.py` turns HA's raw forecast into the exact JSON the client's `WeatherSurface` widget reads. **No model output ever reaches this file.**

Contracts read out of the client, all load-bearing:
- `dynamic_surface_renderer.dart:53` branches on `definition.catalogId == 'weather'`, then looks for a component whose `type` is `weather` and resolves its `location`, `condition`, `temperature` properties. `location` and `condition` must resolve to **strings** and `temperature` to a **number**, or the whole surface renders the "This content can't be shown" fallback.
- `weather_surface.dart` draws the Hourly / 7-day control **itself**. Do not add a `segmentedSelection` component — the range control is built into the widget.
- `_ForecastCell` reads exactly three keys per entry: `label` (string), `temperature` (number), `condition` (string).
- `_forecastFor(range)` returns `data[range]` when it is a list. **A range absent from `data` is what makes tapping it a remote round trip** (`_select` → `dispatch`). So the create carries `hourly` and deliberately **omits** `daily` — that omission *is* the action round-trip requirement.
- `_initialRange()` reads `localState['selectedRange']` first, then `data['selectedRange']`. The server writes `data`; `localState` belongs to the client's cache and must be sent as `{}`.

**Files:**
- Create: `src/eve/ui/weather.py`
- Test: `tests/test_ui_weather.py`

**Interfaces:**
- Consumes: `eve.ui.protocol` (Task 1); the `home.weather` payload shape (Task 3).
- Produces:
  - `decode_forecast(raw: str) -> dict | None`
  - `condition_label(slug: object) -> str`
  - `forecast_cells(entries: object, kind: str, timezone: str) -> list[dict]`
  - `build_create(surface_id: str, forecast: dict, timezone: str) -> dict`
  - `build_range_patch(surface_id: str, value: str, forecast: dict, timezone: str) -> dict | None`
  - `new_surface_id() -> str`
  - `RANGES: tuple[str, str]` — `("hourly", "daily")`
  - `summary(forecast: dict) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui_weather.py`:

```python
"""The `weather` surface, built from Home Assistant's forecast rather than
from anything a model said."""

from __future__ import annotations

import json

from eve.ui import protocol, weather

TORONTO = "America/Toronto"

FORECAST = {
    "entity_id": "weather.home",
    "location": "Home",
    "condition": "partlycloudy",
    "temperature": 21.4,
    "hourly": [
        {"datetime": "2026-08-31T18:00:00+00:00", "condition": "sunny", "temperature": 22.6},
        {"datetime": "2026-08-31T19:00:00+00:00", "condition": "rainy", "temperature": 19.1},
    ],
    "daily": [
        {"datetime": "2026-09-05T12:00:00+00:00", "condition": "pouring", "temperature": 17.0},
    ],
}


def test_a_created_surface_passes_the_protocol_validator():
    operation = weather.build_create("wx-1", FORECAST, TORONTO)
    assert protocol.validate_operation(operation) is None


def test_the_surface_declares_the_weather_catalog_and_binds_its_three_properties():
    """`dynamic_surface_renderer.dart` branches on catalogId == 'weather',
    then resolves exactly these three properties off the component whose type
    is 'weather'."""
    surface = weather.build_create("wx-1", FORECAST, TORONTO)["surface"]

    assert surface["catalogId"] == "weather"
    assert surface["catalogVersion"] == protocol.CATALOG_VERSION
    assert surface["localState"] == {}
    component = surface["components"][0]
    assert component["type"] == "weather"
    assert component["properties"] == {
        "location": "$data.location",
        "condition": "$data.condition",
        "temperature": "$data.temperature",
    }


def test_location_and_condition_resolve_to_strings_and_temperature_to_a_number():
    """Any other type and the renderer draws the "This content can't be
    shown" fallback instead of the card."""
    data = weather.build_create("wx-1", FORECAST, TORONTO)["surface"]["data"]

    assert isinstance(data["location"], str) and data["location"]
    assert isinstance(data["condition"], str) and data["condition"]
    assert isinstance(data["temperature"], (int, float))
    assert not isinstance(data["temperature"], bool)


def test_the_create_carries_hourly_and_deliberately_omits_daily():
    """This omission IS the action round trip. `_forecastFor('daily')` returns
    null for an absent key, and `_select` dispatches a `weather.rangeChanged`
    action rather than switching locally."""
    data = weather.build_create("wx-1", FORECAST, TORONTO)["surface"]["data"]

    assert data["selectedRange"] == "hourly"
    assert isinstance(data["hourly"], list) and data["hourly"]
    assert "daily" not in data


def test_a_forecast_cell_carries_exactly_the_three_keys_the_widget_reads():
    cells = weather.forecast_cells(FORECAST["hourly"], "hourly", TORONTO)

    assert set(cells[0]) == {"label", "temperature", "condition"}
    assert isinstance(cells[0]["temperature"], int)
    assert cells[0]["condition"] == "Sunny"


def test_hourly_labels_are_local_clock_hours_and_daily_labels_are_weekdays():
    """18:00Z on 2026-08-31 is 14:00 in Toronto (EDT), and 2026-09-05 is a
    Saturday. A UTC label would be wrong for every member not at UTC+0."""
    hourly = weather.forecast_cells(FORECAST["hourly"], "hourly", TORONTO)
    daily = weather.forecast_cells(FORECAST["daily"], "daily", TORONTO)

    assert hourly[0]["label"] == "2 PM"
    assert daily[0]["label"] == "Sat"


def test_a_forecast_entry_without_a_usable_temperature_is_dropped_not_faked():
    entries = [
        {"datetime": "2026-08-31T18:00:00+00:00", "condition": "sunny"},
        {"datetime": "not-a-date", "condition": "sunny", "temperature": 20},
        {"datetime": "2026-08-31T19:00:00+00:00", "condition": "rainy", "temperature": 19},
    ]
    cells = weather.forecast_cells(entries, "hourly", TORONTO)

    assert len(cells) == 1
    assert cells[0]["temperature"] == 19


def test_condition_slugs_become_readable_labels():
    assert weather.condition_label("partlycloudy") == "Partly cloudy"
    assert weather.condition_label("lightning-rainy") == "Thunderstorms"
    assert weather.condition_label("clear-night") == "Clear"
    assert weather.condition_label("brand-new-slug") == "Brand new slug"
    assert weather.condition_label(None) == "Unknown"


def test_a_range_patch_sets_both_the_range_and_its_forecast():
    operation = weather.build_range_patch("wx-1", "daily", FORECAST, TORONTO)

    assert protocol.validate_operation(operation) is None
    assert operation["op"] == "patch"
    assert operation["surfaceId"] == "wx-1"
    assert operation["patch"]["dataPatch"]["selectedRange"] == "daily"
    assert operation["patch"]["dataPatch"]["daily"][0]["label"] == "Sat"


def test_a_range_the_home_publishes_nothing_for_produces_no_patch():
    """No patch means no `custom` frame, which is exactly the failure the
    client's own contract describes: the surface keeps its last valid data and
    offers a retry. A patch full of nothing would instead look like success
    and render an empty card."""
    assert weather.build_range_patch("wx-1", "daily", {"daily": []}, TORONTO) is None


def test_decode_forecast_treats_an_error_string_as_a_failure():
    """`eve.tools_client.invoke` answers with a JSON string on success and a
    human-readable `error: ...` on every failure - so the parse failure IS the
    failure signal, and there is no exception to catch."""
    assert weather.decode_forecast("error: eve-tools unavailable (ConnectError)") is None
    assert weather.decode_forecast("null") is None


def test_decode_forecast_rejects_a_payload_with_no_usable_temperature():
    """A non-numeric temperature would render the whole-surface fallback on
    the client. Catch it here, where Eve can still answer in prose."""
    assert weather.decode_forecast(json.dumps({"temperature": None})) is None
    assert weather.decode_forecast(json.dumps({"temperature": True})) is None
    assert weather.decode_forecast(json.dumps({"temperature": 20})) == {"temperature": 20}


def test_surface_ids_are_unique_per_card():
    assert weather.new_surface_id() != weather.new_surface_id()


def test_summary_gives_the_model_a_short_sentence_not_the_payload():
    text = weather.summary(FORECAST)

    assert "Home" in text
    assert "Partly cloudy" in text
    assert len(text) < 160
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ui_weather.py -q`
Expected: FAIL — `ImportError: cannot import name 'weather' from 'eve.ui'`

- [ ] **Step 3: Write the implementation**

Create `src/eve/ui/weather.py`:

```python
"""The `weather` surface: the one catalog type V1 ships.

Everything the client renders is assembled HERE, server-side, out of Home
Assistant's own forecast. The model chooses *whether* to show a card, never
what is in it. A model asked to hand-write thirteen-component JSON produces
`component-schema` rejections and invented temperatures; a model that calls
one no-argument tool cannot do either.

The client contracts this file is shaped by (flutter-open-assistant):
`dynamic_surface_renderer.dart:53` branches on `catalogId == 'weather'` and
resolves `location`/`condition`/`temperature` off the component whose `type`
is `weather`, requiring two strings and a number or it draws the
whole-surface fallback. `weather_surface.dart` draws the Hourly / 7-day
control ITSELF, so no `segmentedSelection` component belongs here.
`_ForecastCell` reads exactly `label`, `temperature`, `condition`.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from eve.ui import protocol

CATALOG_ID = "weather"
RANGES = ("hourly", "daily")

# ponytail: the widget lays cells out in a `Wrap`, and the surface has a
# 48KiB ceiling. Twelve hours and seven days is a card; forty-eight hours is
# a spreadsheet nobody reads on a phone.
_CELLS = {"hourly": 6, "daily": 7}

# Home Assistant's closed condition vocabulary. Anything outside it is
# de-slugged rather than dropped - a new HA condition should read a little
# plain, not blank out the card.
_CONDITIONS = {
    "clear-night": "Clear",
    "cloudy": "Cloudy",
    "exceptional": "Exceptional",
    "fog": "Fog",
    "hail": "Hail",
    "lightning": "Lightning",
    "lightning-rainy": "Thunderstorms",
    "partlycloudy": "Partly cloudy",
    "pouring": "Heavy rain",
    "rainy": "Rain",
    "snowy": "Snow",
    "snowy-rainy": "Sleet",
    "sunny": "Sunny",
    "windy": "Windy",
    "windy-variant": "Windy",
}


def new_surface_id() -> str:
    """Unique per card, not per thread. The client addresses a surface by this
    id and the action envelope carries it back, so nothing server-side has to
    remember it between turns."""
    return f"wx-{uuid.uuid4().hex[:8]}"


def condition_label(slug: object) -> str:
    if not isinstance(slug, str) or not slug:
        return "Unknown"
    if slug in _CONDITIONS:
        return _CONDITIONS[slug]
    return slug.replace("-", " ").replace("_", " ").capitalize()


def decode_forecast(raw: str) -> dict | None:
    """The `home.weather` payload, or None.

    `eve.tools_client.invoke` answers with a JSON string on success and a
    human-readable `error: ...` string on EVERY failure, so a parse failure is
    the failure signal - there is no exception to catch. The temperature check
    belongs here rather than in the renderer's lap: a non-numeric temperature
    makes the client draw its whole-surface fallback, and this is the last
    place Eve can still choose prose instead.
    """
    try:
        forecast = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(forecast, dict):
        return None
    temperature = forecast.get("temperature")
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        return None
    return forecast


def forecast_cells(entries: object, kind: str, timezone: str) -> list[dict]:
    """`{label, temperature, condition}` per cell - exactly the three keys
    `_ForecastCell` reads, and nothing else.

    An entry missing a parseable timestamp or a numeric temperature is
    DROPPED, never defaulted: a card showing `0°` for an hour HA said nothing
    about is worse than a card with one fewer cell.
    """
    zone = ZoneInfo(timezone)
    limit = _CELLS.get(kind, _CELLS["daily"])
    cells: list[dict] = []
    for entry in (entries if isinstance(entries, list) else [])[:limit]:
        if not isinstance(entry, dict):
            continue
        moment = _local_moment(entry.get("datetime"), zone)
        temperature = entry.get("temperature")
        if moment is None or isinstance(temperature, bool):
            continue
        if not isinstance(temperature, (int, float)):
            continue
        cells.append(
            {
                "label": _label(moment, kind),
                "temperature": round(temperature),
                "condition": condition_label(entry.get("condition")),
            }
        )
    return cells


def build_create(surface_id: str, forecast: dict, timezone: str) -> dict:
    """The `create` operation for one weather card.

    `daily` is deliberately ABSENT from `data`. `_forecastFor('daily')`
    returns null for a missing key, so `_select('daily')` dispatches a
    `weather.rangeChanged` action instead of switching locally - that omission
    is what makes the round trip in `eve.ui.actions` reachable at all. Adding
    `"daily": null` would work identically (null is not a list) but reads like
    an oversight; leaving the key out states the intent.
    """
    return {
        "protocol": protocol.PROTOCOL,
        "op": "create",
        "surface": {
            "surfaceId": surface_id,
            "catalogId": CATALOG_ID,
            "catalogVersion": protocol.CATALOG_VERSION,
            "components": [
                {
                    "id": "weather",
                    "type": "weather",
                    "properties": {
                        "location": "$data.location",
                        "condition": "$data.condition",
                        "temperature": "$data.temperature",
                    },
                    "children": [],
                }
            ],
            "data": {
                "location": _location(forecast),
                "condition": condition_label(forecast.get("condition")),
                "temperature": round(forecast["temperature"]),
                "selectedRange": "hourly",
                "hourly": forecast_cells(forecast.get("hourly"), "hourly", timezone),
            },
            # Never seeded server-side: `localState` is the client's own
            # presentation memory, restored from its cache on reopen. A value
            # here would fight `_mergeCachedLocalState` for it.
            "localState": {},
        },
    }


def build_range_patch(
    surface_id: str, value: str, forecast: dict, timezone: str
) -> dict | None:
    """The `patch` answering one `weather.rangeChanged` tap, or None when the
    home publishes nothing for that range.

    None matters: emitting no frame is how the client learns the action
    failed (its contract keeps the last valid data, marks the surface `error`
    and offers a retry). A patch carrying an empty list would instead look
    like success and render an empty card.
    """
    cells = forecast_cells(forecast.get(value), value, timezone)
    if not cells:
        return None
    return {
        "protocol": protocol.PROTOCOL,
        "op": "patch",
        "surfaceId": surface_id,
        "patch": {"dataPatch": {"selectedRange": value, value: cells}},
    }


def summary(forecast: dict) -> str:
    """What the MODEL sees as the tool result: one short sentence, so Eve can
    add a line of her own without reading a payload back to the member."""
    return (
        f"Weather card shown: {_location(forecast)}, "
        f"{condition_label(forecast.get('condition'))}, "
        f"{round(forecast['temperature'])} degrees. "
        "Say one short sentence about it; do not list the forecast."
    )


def _location(forecast: dict) -> str:
    location = forecast.get("location")
    return location if isinstance(location, str) and location else "Home"


def _local_moment(value: object, zone: ZoneInfo) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(zone)
    except ValueError:
        return None


def _label(moment: datetime, kind: str) -> str:
    if kind != "hourly":
        return moment.strftime("%a")
    # Built by hand rather than with `%-I %p`: the dash-modifier is a
    # platform extension (glibc/BSD) that is not portable, and this runs in a
    # container.
    hour = moment.hour % 12 or 12
    return f"{hour} {'AM' if moment.hour < 12 else 'PM'}"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_ui_weather.py -q`
Expected: PASS, 15 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve/ui/weather.py tests/test_ui_weather.py
git commit -m "feat(ui): build the weather surface from Home Assistant's forecast"
```

---

### Task 5: The `show_weather` tool and its binding into the graph

The model's entire share of this feature is one no-argument tool call. The tool takes no forecast, no location, and no JSON from the model — it reads Home Assistant, builds the surface, emits it, and hands back one short sentence.

Two mechanisms this task depends on, both verified against the installed LangGraph 1.2.11:
- `@tool(response_format="content_and_artifact")` returning `(content, artifact)` produces a `ToolMessage` whose `content` is what the model sees and whose `artifact` carries the operation dict. Task 8's `persist_ui` node reads that artifact. An `InjectedState` parameter and a `RunnableConfig` parameter are both hidden from `tool_call_schema`, so the model still sees a zero-argument tool.
- `get_stream_writer()` works inside a tool executed by `ToolNode`, and the payload arrives verbatim on `graph.astream(..., stream_mode="custom")`.

The tool is bound **only when the connected client declared the `weather` catalog**. That is why there is no `EVE_DYNAMIC_UI_ENABLED` setting: the capability handshake already answers the same question per run, and a second switch for one question is a second thing to keep in step.

**Files:**
- Create: `src/eve/ui/tools.py`
- Modify: `src/eve/graph.py:56-66` (`_static_tools`), `:136-137` and `:150-155` (both call sites)
- Modify: `prompts/eve.md`
- Test: `tests/test_ui_tools.py`, `tests/test_graph.py` (append)

**Interfaces:**
- Consumes: `eve.ui.stream.supports`/`emit` (Task 2); `eve.ui.weather` builders (Task 4); `eve.tools_client.invoke` (existing); `eve.state.EveState` (existing).
- Produces:
  - `eve.ui.tools.show_weather` — a `BaseTool` named `show_weather`, `response_format="content_and_artifact"`, whose artifact is the `create` operation dict (or `None`).
  - `eve.graph._static_tools(config: RunnableConfig | None = None) -> list` — same list as before, plus `show_weather` when `stream.supports(config, "weather")`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui_tools.py`:

```python
"""`show_weather`: the model's entire share of the dynamic UI feature."""

from __future__ import annotations

import json

import pytest

from eve.ui import protocol, stream, tools

CONFIG = {
    "configurable": {
        "assistant_ui": {
            "protocol": "assistant-ui/1.0",
            "catalogVersion": "1",
            "catalogIds": ["weather"],
        }
    }
}

STATE = {
    "messages": [],
    "member": {
        "sub": "sub-noah",
        "name": "Noah",
        "role": "adult",
        "timezone": "America/Toronto",
        "permissions": [],
        "local_time": "2026-08-31 14:00 EDT",
    },
    "system_prompt": "",
    "memory": None,
    "dynamic_tools": [],
}

PAYLOAD = {
    "entity_id": "weather.home",
    "location": "Home",
    "condition": "partlycloudy",
    "temperature": 21.4,
    "hourly": [
        {"datetime": "2026-08-31T18:00:00+00:00", "condition": "sunny", "temperature": 22}
    ],
    "daily": [],
}


@pytest.fixture
def written(monkeypatch):
    frames: list = []
    monkeypatch.setattr(stream, "get_stream_writer", lambda: frames.append)
    return frames


def _serve(monkeypatch, raw: str):
    async def fake_invoke(tool, arguments, **kwargs):
        assert tool == "home.weather"
        return raw

    monkeypatch.setattr(tools, "invoke", fake_invoke)


async def _call(config=CONFIG):
    """`state` rides in `args`, not in the config: `InjectedState` is hidden
    from `tool_call_schema` (so the model sees a zero-argument tool) but is
    still required by `args_schema`, which is what a direct invoke validates
    against."""
    return await tools.show_weather.ainvoke(
        {
            "name": "show_weather",
            "args": {"state": STATE},
            "id": "c1",
            "type": "tool_call",
        },
        config=config,
    )


def test_the_model_sees_a_tool_with_no_arguments():
    """A tool the model has to fill in is a tool the model can get wrong.
    Neither the injected state nor the config may appear in the call schema."""
    assert tools.show_weather.tool_call_schema.model_json_schema()["properties"] == {}


async def test_it_emits_one_valid_create_and_returns_a_short_sentence(
    monkeypatch, written
):
    _serve(monkeypatch, json.dumps(PAYLOAD))

    message = await _call()

    assert len(written) == 1
    operation = written[0]["assistant_ui"]
    assert protocol.validate_operation(operation) is None
    assert operation["op"] == "create"
    assert "Home" in message.content
    assert message.artifact == operation


async def test_the_artifact_is_the_operation_so_it_can_be_persisted(
    monkeypatch, written
):
    """`persist_ui` copies this artifact into the AI message as a portable
    frame. Without it a reopened session shows the turn with no card, because
    `custom` frames are streamed and never stored. It rides as an ARTIFACT so
    the surface JSON never enters the model's own context."""
    _serve(monkeypatch, json.dumps(PAYLOAD))

    message = await _call()

    assert message.artifact["surface"]["catalogId"] == "weather"
    assert "surfaceId" not in message.content


async def test_the_forecast_labels_use_the_members_own_timezone(monkeypatch, written):
    """18:00Z is 2 PM in Toronto. A UTC label would be wrong for every member
    not at UTC+0, and the timezone is only knowable from injected state."""
    _serve(monkeypatch, json.dumps(PAYLOAD))

    await _call()

    cells = written[0]["assistant_ui"]["surface"]["data"]["hourly"]
    assert cells[0]["label"] == "2 PM"


async def test_a_client_that_declared_nothing_gets_prose_and_no_frame(
    monkeypatch, written
):
    async def fake_invoke(tool, arguments, **kwargs):  # pragma: no cover
        raise AssertionError("must not reach Home Assistant")

    monkeypatch.setattr(tools, "invoke", fake_invoke)

    message = await _call(config={"configurable": {}})

    assert written == []
    assert message.artifact is None
    assert "words" in message.content


async def test_an_eve_tools_failure_degrades_to_prose(monkeypatch, written):
    """The global constraint every Eve tool obeys: a failing external system
    becomes a returned string the model can talk around, never an exception
    that kills the turn."""
    _serve(monkeypatch, "error: eve-tools unavailable (ConnectError)")

    message = await _call()

    assert written == []
    assert message.artifact is None
    assert "weather" in message.content.lower()


async def test_a_rejected_operation_degrades_to_prose(monkeypatch, written):
    """The last line of defence. If a template change ever produces an
    operation the client would refuse, Eve still answers - she just answers in
    words."""
    _serve(monkeypatch, json.dumps(PAYLOAD))
    monkeypatch.setattr(stream, "emit", lambda operation: False)

    message = await _call()

    assert message.artifact is None
    assert "words" in message.content
```

> **Note for the implementer:** `show_weather` takes its member context from
> `Annotated[EveState, InjectedState]`, which `ToolNode` fills from graph
> state. An `InjectedState` parameter is hidden from `tool_call_schema` (so the
> model still sees a zero-argument tool) but is still *required* by
> `args_schema`, so a direct `ainvoke` must pass it in `args` — which is
> exactly what the tests above do. Verified against LangGraph 1.2.11: omitting
> it raises `ValidationError: state Field required`.

Append to `tests/test_graph.py`:

```python
def test_show_weather_is_bound_only_when_the_client_declared_the_catalog():
    """Binding it unconditionally would put a tool in front of the model that
    can only ever answer "your app can't render this" - and would let a
    surface into a transcript no client will ever replay."""
    from eve.graph import _static_tools

    declared = {
        "configurable": {
            "assistant_ui": {
                "protocol": "assistant-ui/1.0",
                "catalogVersion": "1",
                "catalogIds": ["weather"],
            }
        }
    }

    assert "show_weather" in {tool.name for tool in _static_tools(declared)}
    assert "show_weather" not in {tool.name for tool in _static_tools(None)}
    assert "show_weather" not in {tool.name for tool in _static_tools()}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ui_tools.py tests/test_graph.py -q`
Expected: FAIL — `ImportError: cannot import name 'tools' from 'eve.ui'`, and `_static_tools() takes 0 positional arguments`.

- [ ] **Step 3: Write the implementation**

Create `src/eve/ui/tools.py`:

```python
"""The one tool the model gets: put the weather card on screen.

No arguments, no forecast from the model, no JSON from the model. The model's
only decision is WHETHER a card is the right answer; everything in it comes
from Home Assistant through `eve.ui.weather`. That asymmetry is the point -
see ADR 0013.
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from eve.state import EveState
from eve.tools_client import invoke
from eve.ui import stream, weather

_NO_CLIENT_SUPPORT = (
    "This member's app cannot render weather cards. Answer in words instead."
)
_NO_DATA = (
    "Home Assistant did not return the weather, so there is no card to show. "
    "Say so plainly."
)
_REJECTED = (
    "The weather card was rejected before it could be shown. Answer in words "
    "instead."
)


@tool(response_format="content_and_artifact")
async def show_weather(
    state: Annotated[EveState, InjectedState], config: RunnableConfig
) -> tuple[str, dict | None]:
    """Show the family home's live weather card: current conditions plus an
    hourly strip the member can tap to switch to a 7-day forecast.

    Call this INSTEAD of describing the weather in words when a member asks
    about the weather at home. It takes no arguments and needs no forecast
    from you - it reads Home Assistant itself. Do not call it for another
    city: it only knows the home's own weather.
    """
    if not stream.supports(config, weather.CATALOG_ID):
        return (_NO_CLIENT_SUPPORT, None)

    forecast = weather.decode_forecast(await invoke("home.weather", {}))
    if forecast is None:
        return (_NO_DATA, None)

    # `load_context` always stamps `member` before any tool can run, so this
    # is a total lookup in the graph. The fallback is for a direct invoke.
    timezone = (state or {}).get("member", {}).get("timezone") or "UTC"
    operation = weather.build_create(weather.new_surface_id(), forecast, timezone)
    if not stream.emit(operation):
        return (_REJECTED, None)
    return (weather.summary(forecast), operation)
```

> Do NOT give the `state` parameter a default value to make direct invocation
> easier — an `InjectedState` parameter with a default is excluded from
> injection, so the tool would silently stop receiving real state in the graph
> and every forecast label would fall back to UTC.

Now edit `src/eve/graph.py`. Change `_static_tools` (currently lines 56-66) to take the run config:

```python
def _static_tools(config: RunnableConfig | None = None) -> list:
    """Rebuilt per call rather than fixed at import: three switches gate three
    tools, and both `eve` and `tools_node` need the same answer within one
    turn. Settings are lru_cached, so this is a dict lookup.

    `show_weather`'s switch is not a setting but the connected client's own
    capability declaration (`config.configurable.assistant_ui`). A second
    setting for the same question would be a second thing to keep in step, and
    a surface emitted at a client that cannot render it goes into that
    thread's transcript permanently.

    `config` defaults to None so a caller with no run config - and the tests
    that predate this parameter - get the pre-dynamic-UI tool list.
    """
    settings = get_settings()
    tools = list(_BASE_TOOLS)
    if settings.self_authoring_enabled:
        tools.append(write_skill)
    if settings.sandbox_enabled:
        tools.append(propose_tool)
    if ui_stream.supports(config, "weather"):
        tools.append(show_weather)
    return tools
```

Add the imports near the existing `eve.*` imports:

```python
from eve.ui import stream as ui_stream
from eve.ui.tools import show_weather
```

Update both call sites to pass `config`:

```python
        bound_model = model.bind_tools([*_static_tools(config), *dynamic])
```

```python
        node = ToolNode(
            [*_static_tools(config), *dynamic], handle_tool_errors=_handle_tool_error
        )
```

Finally, append to `prompts/eve.md`, as a new section after "What you care about":

```markdown
What you can put on screen:
- When someone asks about the weather at home, show the weather card instead
  of reading a forecast out loud. Add one short sentence of your own - what it
  means for their day - and let the card carry the numbers.
- If the card cannot be shown, answer in words and do not mention that
  anything failed.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ui_tools.py tests/test_graph.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole unit tier — this task changed a shared signature**

Run: `uv run pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/eve/ui/tools.py src/eve/graph.py prompts/eve.md tests/test_ui_tools.py tests/test_graph.py
git commit -m "feat(ui): add the show_weather tool, bound only when the client can render it"
```

---

### Task 6: Recognising an inbound action envelope

A tap on "7-day" is not a separate channel. `AssistantSessionUseCase.submitUiAction` re-runs the agent with the **user turn's content replaced** by the encoded action envelope, so the graph receives it as an ordinary `HumanMessage`.

The exact shape, from `DynamicSurfaceProtocol.encodeAction` (`dynamic_surface_protocol.dart:55-65`):

```
<assistant-ui-action>
{"protocol":"assistant-ui/1.0","sessionId":"…","surfaceId":"wx-1","actionId":"weather.rangeChanged","value":"daily","data":{…}}
</assistant-ui-action>
```

The provider guide says the markers are added "on the portable path". **The client code wraps unconditionally**, LangGraph included (`langgraph_agent_service.dart:297-299` calls `encodeAction` with no branch, and `langgraph_agent_service_test.dart:266-268` asserts the wrapped content on the wire). Parse tolerantly either way.

`data` in the envelope is the surface's current data — attacker-controllable by anyone who can type into their own chat. This code never reads it. The requested range is re-fetched from Home Assistant in Task 7.

**Files:**
- Create: `src/eve/ui/actions.py`
- Test: `tests/test_ui_actions.py`

**Interfaces:**
- Consumes: `eve.ui.protocol.PROTOCOL`, `ACTION_IDS` (Task 1).
- Produces: `parse_action(text: object) -> dict | None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui_actions.py`:

```python
"""Recognising a UI tap in what arrives as ordinary user text."""

from __future__ import annotations

import json

from eve.ui.actions import parse_action

ENVELOPE = {
    "protocol": "assistant-ui/1.0",
    "sessionId": "session-1",
    "surfaceId": "wx-1",
    "actionId": "weather.rangeChanged",
    "value": "daily",
    "data": {"location": "Home", "selectedRange": "hourly"},
}


def _encoded(envelope=None) -> str:
    body = json.dumps(envelope or ENVELOPE)
    return f"<assistant-ui-action>\n{body}\n</assistant-ui-action>"


def test_a_wrapped_envelope_is_recognised():
    """The client wraps unconditionally on every provider, LangGraph
    included - `langgraph_agent_service.dart` calls `encodeAction` with no
    branch."""
    assert parse_action(_encoded()) == ENVELOPE


def test_a_bare_envelope_is_also_recognised():
    """Tolerated so a future client that drops the markers on a native
    channel still works, rather than silently falling through to the model as
    a wall of JSON."""
    assert parse_action(json.dumps(ENVELOPE)) == ENVELOPE


def test_ordinary_member_speech_is_not_an_action():
    assert parse_action("what's the weather like?") is None
    assert parse_action("") is None
    assert parse_action(None) is None
    assert parse_action([{"type": "text", "text": "hello"}]) is None


def test_an_unclosed_or_malformed_envelope_is_not_an_action():
    """Never guess. A half-arrived envelope has to become a normal Eve turn,
    not a patch built from whatever parsed."""
    assert parse_action("<assistant-ui-action>\n{\"protocol\":") is None
    assert parse_action(_encoded()[:-4]) is None
    assert parse_action("<assistant-ui-action>\nnot json\n</assistant-ui-action>") is None


def test_the_wrong_protocol_version_is_not_an_action():
    assert parse_action(_encoded({**ENVELOPE, "protocol": "assistant-ui/2.0"})) is None


def test_an_action_id_outside_the_v1_contract_is_rejected():
    """V1 has exactly one interactive contract. A crafted envelope naming a
    made-up action must not reach a handler."""
    assert parse_action(_encoded({**ENVELOPE, "actionId": "lights.toggle"})) is None


def test_an_envelope_without_a_surface_id_is_rejected():
    envelope = {key: value for key, value in ENVELOPE.items() if key != "surfaceId"}
    assert parse_action(_encoded(envelope)) is None
    assert parse_action(_encoded({**ENVELOPE, "surfaceId": ""})) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ui_actions.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve.ui.actions'`

- [ ] **Step 3: Write the implementation**

Create `src/eve/ui/actions.py`:

```python
"""The inbound half of the protocol.

A tap on a rendered surface is not a separate channel: the client re-runs the
turn with the user message's content REPLACED by an encoded action envelope
(`AssistantSessionUseCase.submitUiAction` -> `LangGraphAgentService.run`), so
it reaches the graph as an ordinary `HumanMessage` and something has to tell
it apart from a member typing.
"""

from __future__ import annotations

import json

from eve.ui import protocol

_OPENING = "<assistant-ui-action>"
_CLOSING = "</assistant-ui-action>"


def parse_action(text: object) -> dict | None:
    """The decoded envelope, or None when `text` is ordinary member speech.

    Never raises and never guesses: anything that is not a complete,
    current-protocol envelope naming a V1 action is None, and a member who
    types the marker by hand gets a normal Eve turn rather than a UI patch.

    Both the wrapped and the bare form are accepted.
    `DynamicSurfaceProtocol.encodeAction` wraps on EVERY provider - the
    provider guide's "on the portable path" describes intent, the Dart code
    has no branch - and the bare form is tolerated so a future native action
    channel needs no change here.

    The envelope's `data` (the surface's current contents) is deliberately not
    trusted or used by any caller: it arrives from the client and the
    requested range is re-read from Home Assistant instead.
    """
    if not isinstance(text, str):
        return None
    body = text.strip()
    if body.startswith(_OPENING) and body.endswith(_CLOSING):
        body = body[len(_OPENING) : -len(_CLOSING)].strip()
    if not body.startswith("{"):
        return None
    try:
        envelope = json.loads(body)
    except ValueError:
        return None
    if not isinstance(envelope, dict):
        return None
    if envelope.get("protocol") != protocol.PROTOCOL:
        return None
    if envelope.get("actionId") not in protocol.ACTION_IDS:
        return None
    surface_id = envelope.get("surfaceId")
    if not isinstance(surface_id, str) or not surface_id:
        return None
    return envelope
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_ui_actions.py -q`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve/ui/actions.py tests/test_ui_actions.py
git commit -m "feat(ui): recognise the inbound assistant-ui action envelope"
```

---

### Task 7: The `ui_action` node and the route into it

An action turn calls no model. It re-reads the forecast, emits one patch, and ends. The graph becomes:

```
                          ┌─> ui_action ─────────────────────────> END
START -> load_context ────┤
                          └─> recall -> eve <-> tools -> … -> END
```

Why the branch sits **after** `load_context` and not at `START`: `load_context` is pure local computation (ADR 0002) and it is where the member's timezone comes from, which the forecast labels need. Why it skips `recall`: there is no question for a model to answer, so an embedding call and a Postgres read would buy nothing. Why it skips `extract`: its input would be a JSON envelope, and `eve.memory.extract` is not the place to discover that.

**Failure is a raised exception here, deliberately** — the one place in Eve where an external-system failure is not swallowed into a returned string. There is no model in this branch to explain anything in prose, and the protocol's own contract for a failed action is an error event: Aegra turns a node exception into an SSE `error` frame, the client's `_handleFrame` emits `AgentError`, and `_failPendingActionSurface` puts the surface into `error` with its last valid data retained and a retry control offered. Returning quietly instead would leave the card spinning on "Loading forecast" forever.

**Files:**
- Modify: `src/eve/ui/actions.py` (append `UiActionError`, `ACTION_LABELS`, `ui_action`)
- Modify: `src/eve/graph.py` (add the node, replace the `load_context -> recall` edge)
- Test: `tests/test_ui_actions.py` (append), `tests/test_graph.py` (append)

**Interfaces:**
- Consumes: `parse_action` (Task 6); `eve.ui.weather.decode_forecast`/`build_range_patch`/`RANGES` (Task 4); `eve.ui.stream.emit` (Task 2); `eve.ui.protocol.frame` (Task 1); `eve.tools_client.invoke`.
- Produces:
  - `eve.ui.actions.UiActionError(RuntimeError)`
  - `eve.ui.actions.ui_action(state: EveState, config: RunnableConfig) -> dict`
  - `eve.graph._route_after_context(state: EveState) -> str` returning `"ui_action"` or `"recall"`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui_actions.py`. The import lines below belong merged into that file's existing import block at the top, not left mid-file:

```python
# -> move these up to the top of the file
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from eve.ui import actions as actions_module
from eve.ui import protocol, stream

STATE_MEMBER = {
    "sub": "sub-noah",
    "name": "Noah",
    "role": "adult",
    "timezone": "America/Toronto",
    "permissions": [],
    "local_time": "2026-08-31 14:00 EDT",
}

PAYLOAD = {
    "entity_id": "weather.home",
    "location": "Home",
    "condition": "partlycloudy",
    "temperature": 21.4,
    "hourly": [],
    "daily": [
        {"datetime": "2026-09-05T12:00:00+00:00", "condition": "rainy", "temperature": 17}
    ],
}


def _state(text: str) -> dict:
    return {
        "messages": [HumanMessage(content=text, id="h1")],
        "member": STATE_MEMBER,
        "system_prompt": "",
        "memory": None,
        "dynamic_tools": [],
    }


@pytest.fixture
def written(monkeypatch):
    frames: list = []
    monkeypatch.setattr(stream, "get_stream_writer", lambda: frames.append)
    return frames


def _serve(monkeypatch, payload):
    async def fake_invoke(tool, arguments, **kwargs):
        assert tool == "home.weather"
        return json.dumps(payload) if isinstance(payload, dict) else payload

    monkeypatch.setattr(actions_module, "invoke", fake_invoke)


async def test_a_range_tap_emits_one_patch_for_that_surface(monkeypatch, written):
    _serve(monkeypatch, PAYLOAD)

    await actions_module.ui_action(_state(_encoded()), {})

    assert len(written) == 1
    operation = written[0]["assistant_ui"]
    assert protocol.validate_operation(operation) is None
    assert operation["op"] == "patch"
    assert operation["surfaceId"] == "wx-1"
    assert operation["patch"]["dataPatch"]["selectedRange"] == "daily"


async def test_the_forecast_is_refetched_not_taken_from_the_envelope(monkeypatch, written):
    """The envelope's `data` arrives from the client. Trusting it would let a
    crafted envelope choose what the card says."""
    _serve(monkeypatch, PAYLOAD)
    envelope = {**ENVELOPE, "data": {"daily": [{"label": "X", "temperature": 99, "condition": "Nope"}]}}

    await actions_module.ui_action(_state(_encoded(envelope)), {})

    cells = written[0]["assistant_ui"]["patch"]["dataPatch"]["daily"]
    assert cells[0]["label"] == "Sat"
    assert cells[0]["temperature"] == 17


async def test_the_turn_leaves_a_readable_transcript_behind(monkeypatch, written):
    """Two things at once. The raw envelope is replaced in place (same message
    id, so `add_messages` overwrites rather than appends) so a reopened session
    does not show a user bubble full of JSON. And the patch is written into an
    AI message as a portable frame, because `custom` frames are streamed and
    never stored - `loadHistory` replays `values.messages` and nothing else."""
    _serve(monkeypatch, PAYLOAD)

    result = await actions_module.ui_action(_state(_encoded()), {})

    human, ai = result["messages"]
    assert isinstance(human, HumanMessage)
    assert human.id == "h1"
    assert human.content == "Show the 7-day forecast."
    assert isinstance(ai, AIMessage)
    assert ai.content.startswith("<assistant-ui>\n")
    assert ai.content.endswith("\n</assistant-ui>")
    assert json.loads(ai.content.splitlines()[1])["op"] == "patch"


async def test_an_unsupported_range_raises(monkeypatch, written):
    _serve(monkeypatch, PAYLOAD)

    with pytest.raises(actions_module.UiActionError):
        await actions_module.ui_action(_state(_encoded({**ENVELOPE, "value": "yearly"})), {})

    assert written == []


async def test_a_failed_forecast_raises_so_the_client_can_offer_a_retry(
    monkeypatch, written
):
    """The one place in Eve where a failing external system is not swallowed
    into a returned string: there is no model in this branch, and the
    protocol's failure contract IS an error event - the surface keeps its last
    valid data, goes to `error`, and offers a retry. Returning quietly would
    leave the card spinning on "Loading forecast"."""
    _serve(monkeypatch, "error: eve-tools unavailable (ConnectError)")

    with pytest.raises(actions_module.UiActionError):
        await actions_module.ui_action(_state(_encoded()), {})

    assert written == []


async def test_a_range_the_home_publishes_nothing_for_raises(monkeypatch, written):
    _serve(monkeypatch, {**PAYLOAD, "daily": []})

    with pytest.raises(actions_module.UiActionError):
        await actions_module.ui_action(_state(_encoded()), {})
```

Append to `tests/test_graph.py`:

```python
async def test_a_ui_action_turn_skips_recall_the_model_and_extraction(monkeypatch):
    """An action turn has no question in it. Routing it through `recall` would
    spend an embedding call and a Postgres read on a tap, and routing it
    through `extract` would feed a JSON envelope to the extractor."""
    import json

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    called = {"recall": False, "model": False, "extract": False}

    async def spy_recall(state, config):
        called["recall"] = True
        return {"memory": None}

    async def spy_extract(state, config):
        called["extract"] = True
        return {}

    def spy_factory(_tier):
        called["model"] = True
        return FakeToolCallingModel(messages=iter([AIMessage(content="Hi.")]))

    async def fake_invoke(tool, arguments, **kwargs):
        return json.dumps(
            {
                "location": "Home",
                "condition": "sunny",
                "temperature": 20,
                "hourly": [],
                "daily": [
                    {
                        "datetime": "2026-09-05T12:00:00+00:00",
                        "condition": "rainy",
                        "temperature": 17,
                    }
                ],
            }
        )

    monkeypatch.setattr("eve.ui.actions.invoke", fake_invoke)

    envelope = json.dumps(
        {
            "protocol": "assistant-ui/1.0",
            "sessionId": "s1",
            "surfaceId": "wx-1",
            "actionId": "weather.rangeChanged",
            "value": "daily",
            "data": {},
        }
    )
    app = build_graph(
        model_factory=spy_factory, recall_fn=spy_recall, extract_fn=spy_extract
    ).compile()

    frames = []
    async for mode, chunk in app.astream(
        {"messages": [HumanMessage(f"<assistant-ui-action>\n{envelope}\n</assistant-ui-action>")]},
        CONFIG,
        stream_mode=["custom"],
    ):
        frames.append(chunk)

    assert [frame["assistant_ui"]["op"] for frame in frames] == ["patch"]
    assert called == {"recall": False, "model": False, "extract": False}


async def test_ordinary_speech_still_routes_through_recall(monkeypatch):
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    called = {"recall": False}

    async def spy_recall(state, config):
        called["recall"] = True
        return {"memory": None}

    app = build_graph(
        model_factory=_fake_factory, recall_fn=spy_recall, extract_fn=_no_extract
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert called["recall"] is True
    assert result["messages"][-1].content == "Hi Noah."
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ui_actions.py tests/test_graph.py -q`
Expected: FAIL — `AttributeError: module 'eve.ui.actions' has no attribute 'ui_action'`.

- [ ] **Step 3: Write the implementation**

Append to `src/eve/ui/actions.py` (and add `from langchain_core.messages import AIMessage, HumanMessage`, `from langchain_core.runnables import RunnableConfig`, `from eve.state import EveState`, `from eve.tools_client import invoke`, `from eve.ui import stream, weather` to its imports):

```python
class UiActionError(RuntimeError):
    """A UI action that could not be answered.

    Raised, not returned - the single deliberate exception to Eve's "every
    external call degrades to a returned string" rule, and only in this
    branch. There is no model here to explain anything in prose, and the
    protocol's own failure contract IS an error event: Aegra turns a node
    exception into an SSE `error` frame, `LangGraphAgentService._handleFrame`
    emits `AgentError`, and the client's `_failPendingActionSurface` puts the
    surface into `error` with its last VALID data retained and an Ink retry
    control offered. Returning quietly instead would leave the card spinning
    on "Loading forecast" with nothing to say why.
    """


# What the raw envelope is replaced with in the transcript. One owner for the
# literal: a reopened session renders these as the member's own words.
ACTION_LABELS = {
    "hourly": "Show the hourly forecast.",
    "daily": "Show the 7-day forecast.",
}


async def ui_action(state: EveState, config: RunnableConfig) -> dict:
    """Answer one `weather.rangeChanged` tap with one patch. No model call.

    Re-reads Home Assistant rather than trusting the envelope's `data`, which
    arrives from the client.
    """
    last = state["messages"][-1]
    envelope = parse_action(last.content)
    if envelope is None:
        # Unreachable through `_route_after_context`, which only routes here
        # for an envelope this same function parsed. Defensive, because the
        # alternative is a KeyError from a node with no model to fall back on.
        raise UiActionError("not a UI action")

    value = envelope.get("value")
    if value not in weather.RANGES:
        raise UiActionError("unsupported weather range")

    forecast = weather.decode_forecast(await invoke("home.weather", {}))
    if forecast is None:
        raise UiActionError("the weather could not be read")

    operation = weather.build_range_patch(
        envelope["surfaceId"], value, forecast, state["member"]["timezone"]
    )
    if operation is None:
        raise UiActionError("no forecast for that range")
    if not stream.emit(operation):
        raise UiActionError("the patch was rejected")

    return {
        "messages": [
            # Same id, so `add_messages` REPLACES rather than appends: the raw
            # envelope would otherwise show up as a user bubble full of JSON
            # on reopen, since `loadHistory` renders every non-empty human
            # message it finds.
            HumanMessage(content=ACTION_LABELS[value], id=last.id),
            # The patch, again, as a portable frame. `custom` frames are
            # streamed and never stored, and the client replays a reopened
            # session from `values.messages` alone. A node's message update is
            # not an LLM stream event, so this text is never emitted on
            # `messages` mode - the live client renders from the `custom`
            # frame and only ever sees this through history, where it is
            # stripped from the visible text and from TTS.
            AIMessage(content=protocol.frame([operation])),
        ]
    }
```

Now edit `src/eve/graph.py`. Add to the imports:

```python
from eve.ui.actions import parse_action, ui_action
```

Add the router beside the other module-level helpers:

```python
def _route_after_context(state: EveState) -> str:
    """A UI tap arrives as ordinary user text, because the client re-runs the
    turn with the user message's content replaced by an action envelope. It
    carries no question for a model to answer, so it skips `recall` (an
    embedding call and a Postgres read for nothing) and `extract` (whose input
    would be a JSON envelope): the whole turn is one HTTP call and one frame.

    Branching after `load_context` rather than at START is deliberate -
    `load_context` is pure local computation (ADR 0002), and the member
    timezone the forecast labels need comes from it.
    """
    messages = state["messages"]
    if not messages or not isinstance(messages[-1], HumanMessage):
        return "recall"
    return "ui_action" if parse_action(messages[-1].content) else "recall"
```

Inside `build_graph`, register the node and replace the unconditional edge (currently `builder.add_edge("load_context", "recall")`):

```python
    builder.add_node("ui_action", ui_action)
    builder.add_conditional_edges(
        "load_context",
        _route_after_context,
        {"ui_action": "ui_action", "recall": "recall"},
    )
    builder.add_edge("ui_action", END)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ui_actions.py tests/test_graph.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/eve/ui/actions.py src/eve/graph.py tests/test_ui_actions.py tests/test_graph.py
git commit -m "feat(ui): answer a weather range tap in a model-free graph branch"
```

---

### Task 8: Make a created surface survive a session reopen

`custom`-mode frames are **streamed, never stored**. They are not part of `messages`, and `LangGraphAgentService.loadHistory` rebuilds a reopened session from `GET /threads/{id}/state` → `values.messages` and nothing else. Without this task, asking about the weather and relaunching the app shows the turn with no card in it — and the client's own surface cache is no help, because `_mergeCachedLocalState` only restores `localState` onto a surface **the provider's history just returned**.

The fix is one node that copies the turn's `create` operations into the final AI message as a portable `<assistant-ui>` frame. That text is stripped from what the member sees and from TTS on every path, automatically, so it costs nothing visible.

**No double-render.** LangGraph's `messages` stream mode carries LLM token events only; a node returning an updated message emits nothing on it. So the live client renders the card exactly once, from the `custom` frame, and this frame text reaches it only through history.

The operations come from `ToolMessage.artifact` (set by `show_weather`'s `response_format="content_and_artifact"` in Task 5), so the model never sees the surface JSON in its own context.

**Files:**
- Create: `src/eve/ui/persist.py`
- Modify: `src/eve/graph.py` (add the node; re-point `eve`'s END branch through it)
- Test: `tests/test_ui_persist.py`, `tests/test_graph.py` (append)

**Interfaces:**
- Consumes: `eve.ui.protocol.frame`, `MAX_SURFACES_PER_TURN`, `PROTOCOL` (Task 1).
- Produces: `eve.ui.persist.persist_ui(state: EveState) -> dict`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui_persist.py`:

```python
"""`custom` frames are streamed and never stored. This is what makes a card
survive a relaunch."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from eve.ui import protocol
from eve.ui.persist import persist_ui


def _create(surface_id: str) -> dict:
    return {
        "protocol": protocol.PROTOCOL,
        "op": "create",
        "surface": {
            "surfaceId": surface_id,
            "catalogId": "weather",
            "catalogVersion": "1",
            "components": [],
            "data": {},
            "localState": {},
        },
    }


def _tool_message(surface_id: str, call_id: str = "c1") -> ToolMessage:
    return ToolMessage(
        content="Weather card shown.",
        tool_call_id=call_id,
        name="show_weather",
        artifact=_create(surface_id),
    )


async def test_the_turns_surface_is_appended_to_the_final_ai_message():
    state = {
        "messages": [
            HumanMessage(content="what's the weather?", id="h1"),
            AIMessage(content="", id="a1", tool_calls=[]),
            _tool_message("wx-1"),
            AIMessage(content="Nice out there.", id="a2"),
        ]
    }

    result = await persist_ui(state)

    message = result["messages"][0]
    assert message.id == "a2"
    assert message.content.startswith("Nice out there.\n<assistant-ui>\n")
    assert message.content.endswith("\n</assistant-ui>")
    assert json.loads(message.content.splitlines()[2])["op"] == "create"


async def test_a_turn_with_no_surface_changes_nothing():
    """The common case. Every non-weather turn must return an empty update, or
    every AI message in the product gets rewritten for nothing."""
    state = {
        "messages": [
            HumanMessage(content="hello", id="h1"),
            AIMessage(content="Hi Noah.", id="a1"),
        ]
    }

    assert await persist_ui(state) == {}


async def test_only_this_turns_surfaces_are_copied():
    """Scanning back to the last human message, not through the whole thread:
    a card from three turns ago is already in that turn's own AI message, and
    re-appending it would create a duplicate surface id on reopen."""
    state = {
        "messages": [
            HumanMessage(content="turn one", id="h1"),
            _tool_message("wx-old", call_id="c0"),
            AIMessage(content="Older.", id="a1"),
            HumanMessage(content="turn two", id="h2"),
            _tool_message("wx-new", call_id="c1"),
            AIMessage(content="Newer.", id="a2"),
        ]
    }

    result = await persist_ui(state)

    assert "wx-new" in result["messages"][0].content
    assert "wx-old" not in result["messages"][0].content


async def test_a_tool_artifact_that_is_not_an_operation_is_ignored():
    """A dynamically-materialized tool could set an artifact for its own
    reasons. Only an `assistant-ui/1.0` operation belongs in a frame."""
    state = {
        "messages": [
            HumanMessage(content="hi", id="h1"),
            ToolMessage(content="ok", tool_call_id="c1", name="other", artifact={"rows": 3}),
            AIMessage(content="Done.", id="a1"),
        ]
    }

    assert await persist_ui(state) == {}


async def test_no_more_than_eight_surfaces_are_written_into_one_turn():
    """The protocol's per-turn ceiling. The ninth create in one frame makes
    the client reject the WHOLE frame, taking the eight valid surfaces with
    it."""
    messages = [HumanMessage(content="hi", id="h1")]
    for index in range(10):
        messages.append(_tool_message(f"wx-{index}", call_id=f"c{index}"))
    messages.append(AIMessage(content="Done.", id="a1"))

    result = await persist_ui({"messages": messages})

    body = result["messages"][0].content
    assert body.count('"op":"create"') == protocol.MAX_SURFACES_PER_TURN


async def test_a_list_shaped_ai_content_gets_a_text_block_not_a_string():
    """Reasoning-capable models return `content` as a list of typed blocks.
    Concatenating a string onto that would corrupt the message."""
    state = {
        "messages": [
            HumanMessage(content="hi", id="h1"),
            _tool_message("wx-1"),
            AIMessage(content=[{"type": "text", "text": "Nice out."}], id="a1"),
        ]
    }

    result = await persist_ui(state)

    blocks = result["messages"][0].content
    assert isinstance(blocks, list)
    assert blocks[0] == {"type": "text", "text": "Nice out."}
    assert blocks[-1]["type"] == "text"
    assert "<assistant-ui>" in blocks[-1]["text"]
```

Append to `tests/test_graph.py`:

```python
async def test_a_weather_turn_ends_with_the_surface_in_the_transcript(monkeypatch):
    """End to end for the durability guarantee: the card is streamed live on
    `custom` AND left in the persisted AI message, because the client replays
    a reopened session from `values.messages` alone."""
    import json

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    async def fake_invoke(tool, arguments, **kwargs):
        return json.dumps(
            {
                "location": "Home",
                "condition": "sunny",
                "temperature": 20,
                "hourly": [
                    {
                        "datetime": "2026-08-31T18:00:00+00:00",
                        "condition": "sunny",
                        "temperature": 22,
                    }
                ],
                "daily": [],
            }
        )

    monkeypatch.setattr("eve.ui.tools.invoke", fake_invoke)

    call = {"name": "show_weather", "args": {}, "id": "call-wx", "type": "tool_call"}

    def factory(_tier):
        return FakeToolCallingModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[call]),
                    AIMessage(content="Lovely out there."),
                ]
            )
        )

    config = {
        "configurable": {
            **CONFIG["configurable"],
            "assistant_ui": {
                "protocol": "assistant-ui/1.0",
                "catalogVersion": "1",
                "catalogIds": ["weather"],
            },
        }
    }
    app = build_graph(
        model_factory=factory, recall_fn=_no_recall, extract_fn=_no_extract
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("what's the weather?")]}, config)

    final = result["messages"][-1]
    assert final.content.startswith("Lovely out there.\n<assistant-ui>\n")
    assert '"op":"create"' in final.content
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ui_persist.py tests/test_graph.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve.ui.persist'`

- [ ] **Step 3: Write the implementation**

Create `src/eve/ui/persist.py`:

```python
"""Copy this turn's surfaces into the AI message, so a card survives a
relaunch.

`custom`-mode frames are STREAMED, never stored: they are not part of
`messages`, and `LangGraphAgentService.loadHistory` rebuilds a reopened
session from `GET /threads/{id}/state` -> `values.messages` and nothing else.
The client's own surface cache does not save us either -
`_mergeCachedLocalState` restores only `localState`, and only onto a surface
the provider's history just returned. Without this node, asking about the
weather and relaunching the app shows the turn with no card in it.

The same operation written into the AI message as a portable
`<assistant-ui>` frame is stripped from what the member sees and from TTS on
every path, automatically, so it costs nothing visible.

It also does not double-render. LangGraph's `messages` stream mode carries
LLM token events only; a node returning an updated message emits nothing on
it. The live client renders the card once, from the `custom` frame, and this
text reaches it only through history.
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from eve.state import EveState
from eve.ui import protocol

logger = logging.getLogger(__name__)


async def persist_ui(state: EveState) -> dict:
    """`{}` for every turn that emitted no surface - which is nearly all of
    them."""
    operations: list[dict] = []
    final: AIMessage | None = None
    # Backwards to the last human message: a card from an earlier turn is
    # already in that turn's own AI message, and re-appending it would
    # replay a duplicate surfaceId on reopen. Same "read back to the last
    # HumanMessage" idiom as `graph._tool_rounds_this_turn`, for the same
    # reason - the transcript already knows where the turn started.
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, AIMessage) and final is None:
            final = message
        if isinstance(message, ToolMessage) and _is_operation(message.artifact):
            operations.append(message.artifact)
    if not operations or final is None:
        return {}
    operations.reverse()
    if len(operations) > protocol.MAX_SURFACES_PER_TURN:
        # Never silently. The ninth create in one frame makes the client
        # reject the WHOLE frame, taking the eight valid surfaces with it, so
        # the cap has to be enforced here and said out loud.
        logger.warning(
            "assistant_ui surfaces dropped: %d over the per-turn limit",
            len(operations) - protocol.MAX_SURFACES_PER_TURN,
        )
        operations = operations[: protocol.MAX_SURFACES_PER_TURN]
    return {"messages": [_with_frame(final, protocol.frame(operations))]}


def _is_operation(artifact: object) -> bool:
    """A dynamically-materialized tool may set an artifact for its own
    reasons; only an `assistant-ui/1.0` operation belongs in a frame."""
    return isinstance(artifact, dict) and artifact.get("protocol") == protocol.PROTOCOL


def _with_frame(message: AIMessage, text: str) -> AIMessage:
    """Same id, so `add_messages` replaces the message rather than appending
    a second one."""
    if isinstance(message.content, list):
        # Reasoning-capable models return typed content blocks; concatenating
        # a string onto that list would corrupt the message.
        return AIMessage(
            content=[*message.content, {"type": "text", "text": f"\n{text}"}],
            id=message.id,
        )
    return AIMessage(content=f"{message.content}\n{text}", id=message.id)
```

Edit `src/eve/graph.py`. Add the import:

```python
from eve.ui.persist import persist_ui
```

Register the node and re-point `eve`'s END branch through it (replacing the existing `add_conditional_edges("eve", ...)` line and the `extract -> END` wiring order):

```python
    builder.add_node("persist_ui", persist_ui)
    builder.add_conditional_edges(
        "eve", tools_condition, {"tools": "tools", END: "persist_ui"}
    )
    builder.add_edge("persist_ui", "extract")
```

Leave `builder.add_edge("extract", END)` as it is.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ui_persist.py tests/test_graph.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole unit tier — this task rewired the graph**

Run: `uv run pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/eve/ui/persist.py src/eve/graph.py tests/test_ui_persist.py tests/test_graph.py
git commit -m "feat(ui): persist a turn's surfaces so a card survives a reopen"
```

---

### Task 9: End-to-end coverage over the real HTTP boundary, and the docs

Everything so far fakes `eve.tools_client.invoke`. This task exercises the real hop: graph → `eve-tools` over HTTP → stub Home Assistant → surface → `custom` frame. `tests/test_specialists_integration.py` is the pattern — in-process graph, real services below it, only the model faked.

The action path is the cheap and complete one: it calls **no model at all**, so a single integration test covers routing, the HTTP relay, forecast normalisation, validation, emission, and the persisted frame.

**Files:**
- Create: `tests/test_ui_integration.py`
- Create: `docs/adr/0013-dynamic-ui-is-server-built.md`
- Modify: `docs/architecture.md` (the graph section, the module map, the decision-record list)

**Interfaces:**
- Consumes: everything from Tasks 1–8. Produces no new interface.

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_ui_integration.py`:

```python
"""Integration coverage for the dynamic chat UI over the real HTTP boundary:
the graph, a real (locally-run) eve-tools process, and a stub Home Assistant
behind it. Only the model is faked.

Requires `docker compose -f docker-compose.test.yml up -d`? No - this tier
needs neither Postgres nor Redis, only the `eve_tools_server` fixture, which
starts eve-tools and the stub HA itself. Marked `integration` because it binds
real ports and spawns real processes.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from eve.family import Family, Member
from eve.graph import build_graph
from eve.ui import protocol
from tests.conftest import FakeToolCallingModel

pytestmark = pytest.mark.integration

NOAH = Member(
    sub="sub-noah",
    name="Noah",
    role="adult",
    timezone="America/Toronto",
    permissions=frozenset({"home.control"}),
)

CAPABLE_CONFIG = {
    "configurable": {
        "langgraph_auth_user": {"identity": "sub-noah"},
        "assistant_ui": {
            "protocol": "assistant-ui/1.0",
            "catalogVersion": "1",
            "catalogIds": [
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
            ],
        },
    }
}


async def _no_recall(state, config):
    return {"memory": None}


async def _no_extract(state, config):
    return {}


@pytest.fixture
def wired(eve_tools_server, monkeypatch):
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")
    monkeypatch.setenv("EVE_TOOLS_BASE_URL", eve_tools_server)
    monkeypatch.setenv("EVE_TOOLS_API_KEY", "test-key")
    from eve.settings import get_settings

    get_settings.cache_clear()
    return eve_tools_server


async def test_a_range_tap_round_trips_through_real_eve_tools(wired):
    """The whole inbound path with nothing faked below the graph: route the
    envelope, relay to eve-tools, read the stub HA's daily forecast, normalise
    it, validate it, and emit one patch on `custom`."""
    envelope = json.dumps(
        {
            "protocol": "assistant-ui/1.0",
            "sessionId": "session-1",
            "surfaceId": "wx-1",
            "actionId": "weather.rangeChanged",
            "value": "daily",
            "data": {},
        }
    )

    def unused_factory(_tier):  # pragma: no cover
        raise AssertionError("an action turn must not reach a model")

    app = build_graph(
        model_factory=unused_factory, recall_fn=_no_recall, extract_fn=_no_extract
    ).compile()

    frames = []
    async for _mode, chunk in app.astream(
        {
            "messages": [
                HumanMessage(
                    f"<assistant-ui-action>\n{envelope}\n</assistant-ui-action>"
                )
            ]
        },
        CAPABLE_CONFIG,
        stream_mode=["custom"],
    ):
        frames.append(chunk["assistant_ui"])

    assert len(frames) == 1
    assert protocol.validate_operation(frames[0]) is None
    patch = frames[0]["patch"]["dataPatch"]
    assert patch["selectedRange"] == "daily"
    assert len(patch["daily"]) == 7
    assert set(patch["daily"][0]) == {"label", "temperature", "condition"}
    assert patch["daily"][0]["condition"] == "Rain"


async def test_a_weather_request_streams_a_card_and_leaves_it_in_history(wired):
    """The outbound path: the model calls `show_weather`, the surface is built
    from the stub HA's real payload, streamed on `custom`, and left in the AI
    message so a reopened session still has it."""
    call = {"name": "show_weather", "args": {}, "id": "call-wx", "type": "tool_call"}

    def factory(_tier):
        return FakeToolCallingModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[call]),
                    AIMessage(content="Grab a jacket."),
                ]
            )
        )

    app = build_graph(
        model_factory=factory, recall_fn=_no_recall, extract_fn=_no_extract
    ).compile()
    inputs = {"messages": [HumanMessage("what's the weather at home?")]}

    frames = []
    async for _mode, chunk in app.astream(
        inputs, CAPABLE_CONFIG, stream_mode=["custom"]
    ):
        frames.append(chunk["assistant_ui"])

    assert len(frames) == 1
    surface = frames[0]["surface"]
    assert surface["catalogId"] == "weather"
    assert surface["data"]["location"] == "Home"
    assert surface["data"]["condition"] == "Partly cloudy"
    assert surface["data"]["temperature"] == 21
    assert surface["data"]["selectedRange"] == "hourly"
    assert len(surface["data"]["hourly"]) == 6
    # Absent on purpose: this is what makes tapping 7-day a round trip.
    assert "daily" not in surface["data"]

    result = await app.ainvoke(inputs, CAPABLE_CONFIG)
    assert "<assistant-ui>" in result["messages"][-1].content


async def test_no_card_is_emitted_at_a_client_that_declared_nothing(wired):
    """Fails closed. The tool is not even bound, so the model cannot call it,
    and nothing unreadable lands in the transcript."""
    app = build_graph(
        model_factory=lambda _tier: FakeToolCallingModel(
            messages=iter([AIMessage(content="It's mild out.")])
        ),
        recall_fn=_no_recall,
        extract_fn=_no_extract,
    ).compile()

    frames = []
    async for _mode, chunk in app.astream(
        {"messages": [HumanMessage("what's the weather?")]},
        {"configurable": {"langgraph_auth_user": {"identity": "sub-noah"}}},
        stream_mode=["custom"],
    ):
        frames.append(chunk)

    assert frames == []
```

- [ ] **Step 2: Run the integration tier to verify it fails, then passes**

Run: `uv run pytest tests/test_ui_integration.py -m integration -q`

Expected before Tasks 1–8 land: collection errors. Expected now: PASS, 3 tests. If `test_a_range_tap_round_trips_through_real_eve_tools` fails on the forecast being empty, the stub's `/api/services/weather/get_forecasts` route is declared **after** the generic `/api/services/{domain}/{service}` route — FastAPI matches in declaration order and the generic handler is swallowing it (Task 3, Step 1).

- [ ] **Step 3: Write ADR 0013**

Create `docs/adr/0013-dynamic-ui-is-server-built.md`:

```markdown
# 13. Dynamic UI surfaces are built server-side and only triggered by the model

**Status:** Accepted
**Date:** 2026-08-31

## Context

The Flutter client renders agent-declared interactive surfaces inline in a
chat turn - a weather card today, over a closed thirteen-type catalog
(`assistant-ui/1.0`). A provider drives it by sending create/patch/delete
operations, either natively on LangGraph's `custom` stream mode or as a
portable `<assistant-ui>` frame embedded in assistant text.

The obvious implementation is to teach the model the protocol and let it emit
surface JSON. That fails three ways at once. The client validates hard and
rejects **silently**: an illegal component type, a property the type does not
declare, a malformed `$data.` binding, or a `temperature` that resolves to a
string all render one neutral "This content can't be shown" card, or on the
native path are dropped with a log line that never leaves the phone. Second,
a model asked for a forecast will produce one whether or not it has data -
which is a card confidently displaying invented weather. Third, the surface
JSON would occupy the model's own context on every turn it appears in.

## Decision

The model's only decision is **whether** a surface is the right answer. It
calls a no-argument `show_weather` tool. Everything in the card -
structure, bindings, data, forecast cells, labels - is assembled by
`eve.ui.weather` from Home Assistant's own `weather.get_forecasts` response,
validated against a server-side mirror of the client's validator
(`eve.ui.protocol`), and only then emitted.

Consequences of that shape, each deliberate:

- **Capabilities gate the tool, not just the emission.** The client declares
  what it can render at `config.configurable.assistant_ui`, and
  `show_weather` is bound into the model's tool list only when that
  declaration names the `weather` catalog. Failing closed is right because a
  surface is not free: a frame emitted at a client that cannot render it
  stays in that thread's transcript permanently.
- **Every operation is streamed on `custom` AND written into the AI message
  as a portable frame.** `custom` frames are streamed and never stored; the
  client replays a reopened session from `values.messages` alone. Two
  mechanisms is not redundancy - it is one for live rendering and one for
  durability, and they do not collide because LangGraph's `messages` stream
  mode carries LLM token events only, so a node's message rewrite is invisible
  to the live client.
- **The action round trip has no model in it.** A tap arrives as the next
  turn's user text, so `load_context` routes an action envelope to a
  `ui_action` node that re-reads the forecast and emits one patch. It skips
  `recall` (an embedding call for a tap) and `extract` (whose input would be a
  JSON envelope), and the raw envelope is replaced in the transcript with a
  readable sentence.
- **The envelope's `data` is never trusted.** It arrives from the client and
  the requested range is re-read from Home Assistant instead.

## Consequences

**The validator is a second copy of a client-side one** (`eve.ui.protocol`
mirrors `dynamic_surface_protocol.dart`), and it will drift the day the
catalog grows. That is accepted because the client rejects silently: without
this copy there is no server-side signal that a surface was ever refused. The
client already carries the same duplication three times over by design -
domain, data layer, renderer - and its own comment says to keep them in
lockstep by hand.

**The card shows the home's weather and nothing else.** There is one HA
weather entity, `show_weather` takes no location, and Eve is told in
`prompts/eve.md` not to offer a card for another city. A member asking about
Toronto while away from home gets prose. Adding a second location means adding
a data source, not a tool argument.

**Per-turn and per-minute protocol limits are structural, not counted.**
`show_weather` emits at most one surface per call and the outer tool loop is
bounded to `EVE_MAX_TOOL_LOOP_ITERATIONS` (6) rounds, which is below the
8-surface ceiling; one action turn emits one patch and requires one human tap,
which cannot reach 30 updates a minute. `eve.ui.persist` enforces the
8-surface cap explicitly, and says so in a log line, because the ninth create
in one frame makes the client reject the whole frame. If a future surface type
emits in a loop, that reasoning stops holding and a real per-run counter is
what to add.

**`ui_action` raises where the rest of Eve returns a string.** Every other
external call in this codebase degrades to a returned error string, because
its result goes to a model that can talk around it. There is no model in that
branch, and the protocol's own failure contract is an error event: an
exception becomes an SSE `error` frame, the client marks the surface `error`
with its last valid data retained and offers a retry. Returning quietly would
leave the card spinning on "Loading forecast" with nothing to say why. The
cost is that a failed action turn leaves the raw envelope in the thread's
history, where it renders as a user bubble of JSON.
```

- [ ] **Step 4: Update `docs/architecture.md`**

Three edits.

**a.** Replace the graph diagram at the top of "## The graph" and add the two new nodes to the bullet list:

```
                          ┌─> ui_action ────────────────────────────────> END
START -> load_context ────┤
                          └─> recall -> eve <-> tools -> persist_ui -> extract -> END
```

Change "Five nodes, wired in `src/eve/graph.py`" to "Seven nodes, wired in `src/eve/graph.py`", and add these two bullets after the `tools` bullet:

```markdown
- **`ui_action`** (`src/eve/ui/actions.py`) answers a tap on a rendered
  dynamic surface. The client re-runs the turn with the user message's content
  replaced by an `<assistant-ui-action>` envelope, so `load_context` routes it
  here instead of to `recall`: the branch calls no model, re-reads the
  forecast from Home Assistant rather than trusting the envelope's `data`,
  emits one `patch` on the `custom` stream, and replaces the raw envelope in
  the transcript with a readable sentence. It is the one place in Eve where a
  failed external call raises rather than returning a string — see
  [ADR 0013](adr/0013-dynamic-ui-is-server-built.md).
- **`persist_ui`** (`src/eve/ui/persist.py`) copies whatever surfaces the turn
  emitted into the final AI message as a portable `<assistant-ui>` frame.
  `custom` frames are streamed and never stored, and the client replays a
  reopened session from `values.messages` alone, so without this a card
  vanishes on relaunch. The frame text is never streamed live (a node's
  message update is not an LLM token event) and is stripped from what the
  member sees and from TTS on every path.
```

**b.** Add to the module map, after the `skills/` block:

```
  ui/
    protocol.py     # the assistant-ui/1.0 contract, its validator, the portable frame
    stream.py       # client capabilities in, custom-mode frames out
    weather.py      # the weather surface, built from HA's forecast; no model output
    tools.py        # show_weather: the model's whole share of the feature
    actions.py      # inbound action envelope + the model-free ui_action node
    persist.py      # copy this turn's surfaces into the AI message for history
```

**c.** Append to "## Decision records":

```markdown
- [ADR 0013 — Dynamic UI surfaces are built server-side and only triggered by the model](adr/0013-dynamic-ui-is-server-built.md)
```

- [ ] **Step 5: Run every tier**

```bash
uv run pytest -q
uv run pytest -m integration -q
```

Expected: PASS. The integration tier needs `docker compose -f docker-compose.test.yml up -d` for the pre-existing Aegra tests; `tests/test_ui_integration.py` itself needs only the `eve_tools_server` fixture.

- [ ] **Step 6: Commit**

```bash
git add tests/test_ui_integration.py docs/adr/0013-dynamic-ui-is-server-built.md docs/architecture.md
git commit -m "test(ui): cover the dynamic UI over the real eve-tools boundary; document it"
```

---

## Verifying against the real client

Nothing above talks to a phone. Once the tasks land, the honest check is the
Flutter app pointed at a local `aegra serve`:

```bash
docker compose -f docker-compose.test.yml up -d
uv run uvicorn eve_tools.app:app --port 8090   # or the real eve-tools
uv run aegra serve
```

Then, in `~/GitHub/open-assistant/flutter-open-assistant`, point the app's
LangGraph endpoint at `http://<host>:2026`, ask "what's the weather at home?",
and confirm: the card renders inline, tapping **7-day** shows the Signal-coloured
"Loading forecast" control and then the daily strip, and force-quitting and
reopening the session still shows the card with the 7-day range selected.

Two failures worth naming in advance:
- **Card never appears, no error.** The capability declaration is not reaching
  the graph. Check that the run body carries
  `config.configurable.assistant_ui` (not `metadata`), and that the assistant
  record Aegra resolved has no `config` that shadows it.
- **Card appears live, gone after relaunch.** `persist_ui` is not on the path
  the turn actually took, or the frame markers are off by a newline —
  `FramedDynamicSurfaceParser` matches `"<assistant-ui>\n"` and
  `"\n</assistant-ui>"` exactly.
