# Model-Authored Dynamic Surfaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the one-tool-per-surface pattern with a single `show_surface(components)` tool the model uses to compose any UI, including forms with inputs and a Save button that sends values back to Eve.

**Architecture:** The model authors the component tree; the server owns the surface envelope and validates the tree against a mirror of the client's validator, returning diagnostics so the model can retry. Two new input component types write to `localState`, which becomes client-mutable for the first time. The bespoke weather surface and its model-less action round trip are deleted outright.

**Tech Stack:** Python 3.12, LangGraph, LangChain, pytest (`asyncio_mode = "auto"`); Flutter/Dart, `flutter_test`, `integration_test`.

**Spec:** `docs/superpowers/specs/2026-09-02-dynamic-surfaces-design.md`

## Global Constraints

- **Two repositories.** Server work is in `/Users/nchalifo/GitHub/eve-ai`. Client work is in `/Users/nchalifo/GitHub/open-assistant/flutter-open-assistant`. Every task states which. Commit separately per repo.
- **`catalogVersion` stays `"1"`.** Never bump it. Additions are new component ids only; `stream.supports()` gates per-id off the client's declared `catalogIds`.
- **`PROTOCOL` stays `"assistant-ui/1.0"`.**
- **Five catalog copies must stay in lockstep by hand:** `eve.ui.protocol` (server), `DynamicUiCapabilities.v1`, `DynamicSurfaceProtocol`, `DynamicSurfaceCatalog` (client), and `skills/build-a-ui/SKILL.md` (prose). Task 7 adds a test for the fifth; the other four have no automated check by design.
- **Every external call degrades to a returned string, never a raised exception.** After Task 1 the codebase has no raising node; do not add one.
- **Server test command:** `uv run pytest` from `/Users/nchalifo/GitHub/eve-ai`.
- **Client test command:** `flutter test` from `/Users/nchalifo/GitHub/open-assistant/flutter-open-assistant`.
- **Server tasks (1-8) land before client tasks (9-15).** The server gates per-type against what the client declares, so a server already shipped is harmless to an un-updated client: it simply never binds input trees.
- **Known one-way consequence:** after Task 9, threads containing weather cards from before this change render the neutral "This content can't be shown" fallback. This is accepted — the frames stay in the transcript, only the bespoke renderer is gone.

---

## File Structure

**Server (`eve-ai`)**

| Path | Responsibility | Change |
|---|---|---|
| `src/eve/ui/protocol.py` | Wire contract + validator mirror | Modify |
| `src/eve/ui/surface.py` | Build a `create` operation from model components | **Create** |
| `src/eve/ui/stream.py` | Capability handshake + `custom` emission | Modify |
| `src/eve/ui/tools.py` | `show_surface`, the model's one UI tool | Modify (rewrite) |
| `src/eve/ui/actions.py` | Inbound envelope parsing + `ui_submit` | Modify |
| `src/eve/ui/persist.py` | Frame durability | **Unchanged** |
| `src/eve/ui/weather.py` | — | **Delete** |
| `src/eve/graph.py` | Node + routing wiring | Modify |
| `skills/build-a-ui/SKILL.md` | Catalog reference + UI guidance | **Create** |
| `prompts/eve.md` | Persona guidance | Modify |
| `docs/adr/0017-model-authored-surfaces.md` | Supersedes ADR 0014 | **Create** |

**Client (`flutter-open-assistant`)**

| Path | Responsibility | Change |
|---|---|---|
| `lib/domain/models/dynamic_ui/dynamic_ui_capabilities.dart` | Advertised catalog | Modify |
| `lib/data/services/agent/dynamic_surface_protocol.dart` | Wire validation | Modify |
| `lib/ui/features/chat/dynamic_ui/dynamic_surface_catalog.dart` | Renderer registration | Modify |
| `lib/ui/features/chat/dynamic_ui/dynamic_surface_renderer.dart` | Component builders | Modify |
| `lib/ui/features/chat/dynamic_ui/weather_surface.dart` | — | **Delete** |
| `lib/domain/models/dynamic_ui/ui_action.dart` | Outbound action | Modify |
| `lib/domain/use_cases/assistant_session_use_case.dart` | Local state merge + cache write | Modify |
| `lib/ui/features/chat/views/chat_message.dart` | Renderer construction | Modify |

---

## Task 1: Delete the weather surface (server)

Deletion first, so later tasks build on a clean protocol rather than working around a type that is going away. The system stays coherent after this task: Eve answers weather in prose, and no dynamic UI exists at all until Task 5.

**Repo:** `eve-ai`

**Files:**
- Delete: `src/eve/ui/weather.py`
- Modify: `src/eve/ui/protocol.py`
- Modify: `src/eve/ui/tools.py`
- Modify: `src/eve/ui/actions.py`
- Modify: `src/eve/graph.py`
- Modify: `prompts/eve.md:21-26`
- Delete: `tests/test_ui_tools.py`
- Modify: `tests/test_ui_protocol.py`, `tests/test_ui_actions.py`, `tests/test_graph.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `protocol.CATALOG_IDS` without `"weather"`; `protocol.ACTION_IDS == frozenset()`; `eve.ui.tools` empty of tools; `eve.ui.actions` exporting only `parse_action`; `graph._route_after_context` always returning `"recall"`.

- [ ] **Step 1: Delete the weather modules and their tests**

```bash
cd /Users/nchalifo/GitHub/eve-ai
git rm src/eve/ui/weather.py tests/test_ui_tools.py
```

- [ ] **Step 2: Strip weather from the protocol**

In `src/eve/ui/protocol.py`:

Remove `"weather"` from `CATALOG_IDS` (leaving twelve ids), and update its comment from "The same thirteen ids" to "The same twelve ids".

Change `ACTION_IDS` to:

```python
# Emptied with the weather surface. Task 2 refills it with `surface.submit`.
ACTION_IDS: frozenset[str] = frozenset()
```

Remove the `"weather"` entry from `_ALLOWED_PROPERTIES`.

Remove `"location"` and `"condition"` from `_STRING_PROPERTIES`, leaving:

```python
_STRING_PROPERTIES = frozenset({"title", "text", "name", "label", "selected"})
```

In `_validate_property`, delete the `temperature` branch:

```python
    if key == "temperature":
        return _number_or_binding(value)
```

Delete the now-unused `_number_or_binding` function entirely.

- [ ] **Step 3: Empty the UI tool module**

Replace the whole contents of `src/eve/ui/tools.py` with:

```python
"""Eve's dynamic-UI tools.

Emptied with the weather surface; `show_surface` lands here in Task 5.
"""

from __future__ import annotations
```

- [ ] **Step 4: Strip the action round trip**

In `src/eve/ui/actions.py`, delete `UiActionError`, `ui_action`, `_RANGE_NAMES`, `_range_name` and `ACTION_LABELS`, and remove the now-unused imports (`AIMessage`, `HumanMessage`, `RunnableConfig`, `EveState`, `invoke`, `stream`, `weather`). Keep `parse_action` and its module docstring, and keep the `protocol` and `json` imports.

- [ ] **Step 5: Unwire the node from the graph**

In `src/eve/graph.py`:

Delete `from eve.ui.actions import parse_action, ui_action` and `from eve.ui.tools import show_weather`; add `from eve.ui.actions import parse_action`.

Delete the `show_weather` binding from `_static_tools`:

```python
    if ui_stream.supports(config, "weather"):
        tools.append(show_weather)
```

and the paragraph of its docstring beginning "`show_weather`'s switch is not a setting".

Replace `_route_after_context`'s body with:

```python
def _route_after_context(state: EveState, config: RunnableConfig) -> str:
    """Every turn goes to `recall`.

    The weather surface's model-less tap branch is gone; Task 6 restores a
    branch here for `surface.submit`, which routes to a node that rewrites
    the envelope and then continues to `recall` rather than ending the turn.
    """
    return "recall"
```

Delete `builder.add_node("ui_action", ui_action)`, `builder.add_edge("ui_action", END)`, and replace the conditional edge with a plain one:

```python
    builder.add_edge("load_context", "recall")
```

- [ ] **Step 6: Remove the weather guidance from the persona**

In `prompts/eve.md`, delete these five lines (currently 21-26):

```
What you can put on screen:
- When someone asks about the weather at home, show the weather card instead
  of reading a forecast out loud. Add one short sentence of your own - what it
  means for their day - and let the card carry the numbers.
- If the card cannot be shown, answer in words and do not mention that
  anything failed.
```

- [ ] **Step 7: Delete the orphaned tests**

In `tests/test_ui_protocol.py`, delete every test asserting on the `weather` component type, `location`/`condition`/`temperature` properties, or `weather.rangeChanged`.

In `tests/test_ui_actions.py`, delete every test of `ui_action` and `UiActionError`, and change the module-level `ENVELOPE` to a shape no longer naming a legal action — every `parse_action` test that expected success now expects `None`, because `ACTION_IDS` is empty:

```python
ENVELOPE = {
    "protocol": "assistant-ui/1.0",
    "sessionId": "session-1",
    "surfaceId": "sf-1",
    "actionId": "surface.submit",
    "value": None,
    "data": {},
}


def test_no_action_is_recognised_while_action_ids_is_empty():
    """Task 1 empties `ACTION_IDS`; Task 2 refills it and this test is
    replaced by the `surface.submit` cases."""
    assert parse_action(_encoded()) is None
```

In `tests/test_graph.py`, delete every test asserting `show_weather` is bound or that an envelope routes to `ui_action`.

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest`
Expected: PASS, with no import errors from the deleted modules.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat(ui)!: delete the weather surface and its action round trip

Clears the way for one generic surface tool. Removes the codebase's only
raising node along with it.

Refs EVE-22"
```

---

## Task 2: Protocol additions (server)

**Repo:** `eve-ai`

**Files:**
- Modify: `src/eve/ui/protocol.py`
- Test: `tests/test_ui_protocol.py`

**Interfaces:**
- Consumes: Task 1's stripped protocol.
- Produces: `CATALOG_IDS` containing `"textField"` and `"numberField"`; `ACTION_IDS == frozenset({"surface.submit"})`; `_ALLOWED_PROPERTIES` entries for the two input types and `"setState"` on `"button"`; `validate_operation` rejecting a button with both or neither of `actionId`/`setState`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ui_protocol.py`:

```python
def _create(components: list[dict]) -> dict:
    return {
        "protocol": "assistant-ui/1.0",
        "op": "create",
        "surface": {
            "surfaceId": "sf-1",
            "catalogId": "column",
            "catalogVersion": "1",
            "components": components,
        },
    }


def test_a_text_field_declares_a_state_key_and_a_label():
    operation = _create(
        [
            {
                "id": "c1",
                "type": "textField",
                "properties": {"stateKey": "exercise", "label": "Exercise"},
            }
        ]
    )
    assert protocol.validate_operation(operation) is None


def test_a_number_field_declares_a_state_key_and_a_label():
    operation = _create(
        [
            {
                "id": "c1",
                "type": "numberField",
                "properties": {"stateKey": "reps", "label": "Reps"},
            }
        ]
    )
    assert protocol.validate_operation(operation) is None


def test_a_state_key_may_not_be_a_binding():
    """`stateKey` names a localState slot to WRITE. A `$data.` binding
    resolves against read-only surface data, so accepting one here would
    describe a write to a value the client cannot address."""
    operation = _create(
        [
            {
                "id": "c1",
                "type": "textField",
                "properties": {"stateKey": "$data.reps", "label": "Reps"},
            }
        ]
    )
    assert protocol.validate_operation(operation) == "component-schema"


def test_an_input_rejects_an_undeclared_property():
    operation = _create(
        [
            {
                "id": "c1",
                "type": "numberField",
                "properties": {"stateKey": "reps", "placeholder": "8"},
            }
        ]
    )
    assert protocol.validate_operation(operation) == "component-schema"


def test_a_button_may_set_local_state():
    operation = _create(
        [
            {
                "id": "c1",
                "type": "button",
                "properties": {"label": "Clear", "setState": {"reps": 0}},
            }
        ]
    )
    assert protocol.validate_operation(operation) is None


def test_a_button_may_submit():
    operation = _create(
        [
            {
                "id": "c1",
                "type": "button",
                "properties": {"label": "Save", "actionId": "surface.submit"},
            }
        ]
    )
    assert protocol.validate_operation(operation) is None


def test_a_button_may_not_do_both():
    """Both would mean one tap with two meanings, and the client would have
    to pick an order the protocol never states."""
    operation = _create(
        [
            {
                "id": "c1",
                "type": "button",
                "properties": {
                    "label": "Save",
                    "actionId": "surface.submit",
                    "setState": {"done": True},
                },
            }
        ]
    )
    assert protocol.validate_operation(operation) == "component-schema"


def test_a_button_may_not_do_neither():
    """A button that does nothing renders as a live control that silently
    ignores taps - worse than the whole-surface fallback, which at least
    says something is wrong."""
    operation = _create([{"id": "c1", "type": "button", "properties": {"label": "Save"}}])
    assert protocol.validate_operation(operation) == "component-schema"


def test_set_state_must_be_a_json_object():
    operation = _create(
        [{"id": "c1", "type": "button", "properties": {"label": "Go", "setState": 3}}]
    )
    assert protocol.validate_operation(operation) == "component-schema"


def test_set_state_values_obey_the_string_ceiling():
    operation = _create(
        [
            {
                "id": "c1",
                "type": "button",
                "properties": {"label": "Go", "setState": {"note": "x" * 2049}},
            }
        ]
    )
    assert protocol.validate_operation(operation) == "string-limit"


def test_surface_submit_is_the_only_action():
    assert protocol.ACTION_IDS == frozenset({"surface.submit"})


def test_an_unknown_action_id_is_rejected():
    operation = _create(
        [
            {
                "id": "c1",
                "type": "button",
                "properties": {"label": "Go", "actionId": "surface.explode"},
            }
        ]
    )
    assert protocol.validate_operation(operation) == "action-schema"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ui_protocol.py -v`
Expected: FAIL — the input-type tests fail with `"component-type"`, the button tests with `"action-schema"` or by passing when they should fail.

- [ ] **Step 3: Implement the protocol changes**

In `src/eve/ui/protocol.py`, add the two ids to `CATALOG_IDS`:

```python
        "expandable",
        "textField",
        "numberField",
```

Refill `ACTION_IDS`:

```python
# One interactive contract: a button that hands the surface's localState back
# to Eve as a turn. A provider cannot invent an action.
ACTION_IDS = frozenset({"surface.submit"})
```

Add the input entries to `_ALLOWED_PROPERTIES` and `setState` to `button`:

```python
    "button": frozenset({"label", "actionId", "actionValue", "setState"}),
    "textField": frozenset({"stateKey", "label"}),
    "numberField": frozenset({"stateKey", "label"}),
```

Replace `_validate_properties` so the button rule, which is the one check needing the component type, has it:

```python
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
    if component_type == "button":
        # Exactly one, never both and never neither. Both would be one tap
        # with two meanings in an order the protocol never states; neither
        # renders a live control that silently ignores taps, which is worse
        # than the fallback because it says nothing is wrong.
        declared = ("actionId" in properties) + ("setState" in properties)
        if declared != 1:
            return "component-schema"
    return None
```

Add the two new property branches to `_validate_property`, before its final `return`:

```python
    if key == "stateKey":
        # A literal key, never a binding: it names a localState slot to
        # WRITE, and `$data.` resolves against read-only surface data. The
        # `$` check is the whole point - without it `$data.reps` validates
        # and the client writes to a key literally called "$data.reps".
        if not isinstance(value, str) or not value or value.startswith("$"):
            return "component-schema"
        return validate_json_value(value)
    if key == "setState":
        if not isinstance(value, dict):
            return "component-schema"
        return validate_json_value(value)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ui_protocol.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/eve/ui/protocol.py tests/test_ui_protocol.py
git commit -m "feat(ui): add input components, setState buttons, and surface.submit

Refs EVE-22"
```

---

## Task 3: The surface builder (server)

**Repo:** `eve-ai`

**Files:**
- Create: `src/eve/ui/surface.py`
- Test: `tests/test_ui_surface.py` (create)

**Interfaces:**
- Consumes: `protocol.PROTOCOL`, `protocol.CATALOG_VERSION`, `protocol.validate_operation`.
- Produces: `surface.new_surface_id() -> str`; `surface.build_create(surface_id: str, components: list) -> dict`; `surface.component_types(components: object) -> set[str]`; `surface.ROOT_CATALOG_ID == "column"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ui_surface.py`:

```python
"""Assembling a model-authored component tree into a create operation."""

from __future__ import annotations

from eve.ui import protocol, surface

COMPONENTS = [
    {
        "id": "c1",
        "type": "card",
        "properties": {"title": "Workout"},
        "children": [
            {
                "id": "c2",
                "type": "numberField",
                "properties": {"stateKey": "reps", "label": "Reps"},
            }
        ],
    }
]


def test_the_server_owns_the_envelope():
    """The model supplies components and nothing else - two fewer fields it
    can get wrong, and `catalogId` is not a decision it has information to
    make."""
    operation = surface.build_create("sf-1", COMPONENTS)
    assert operation["protocol"] == protocol.PROTOCOL
    assert operation["op"] == "create"
    assert operation["surface"]["surfaceId"] == "sf-1"
    assert operation["surface"]["catalogId"] == "column"
    assert operation["surface"]["catalogVersion"] == protocol.CATALOG_VERSION
    assert operation["surface"]["components"] == COMPONENTS


def test_the_built_operation_validates():
    assert protocol.validate_operation(surface.build_create("sf-1", COMPONENTS)) is None


def test_data_is_empty_and_local_state_is_unseeded():
    """No server-side data source exists, so nothing produces `$data.`
    bindings. `localState` stays empty because it is the client's own
    presentation memory - a value here would fight the cache restore for
    it."""
    built = surface.build_create("sf-1", COMPONENTS)["surface"]
    assert built["data"] == {}
    assert built["localState"] == {}


def test_surface_ids_are_unique_per_card():
    assert surface.new_surface_id() != surface.new_surface_id()
    assert surface.new_surface_id().startswith("sf-")


def test_component_types_walks_the_whole_tree():
    """Capability gating checks every type the model used, at any depth -
    a nested input at a client that cannot render it is still a surface
    written permanently into that thread's transcript."""
    assert surface.component_types(COMPONENTS) == {"card", "numberField"}


def test_component_types_tolerates_malformed_input():
    """Runs BEFORE validation, on a tree straight from the model, so it can
    never raise on a shape the validator has not seen yet."""
    assert surface.component_types("nonsense") == set()
    assert surface.component_types([{"type": 3}, "x", {"children": "y"}]) == set()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ui_surface.py -v`
Expected: FAIL with `ImportError: cannot import name 'surface'`

- [ ] **Step 3: Write the implementation**

Create `src/eve/ui/surface.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ui_surface.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/eve/ui/surface.py tests/test_ui_surface.py
git commit -m "feat(ui): add the model-authored surface builder

Refs EVE-22"
```

---

## Task 4: Per-type capability gating (server)

**Repo:** `eve-ai`

**Files:**
- Modify: `src/eve/ui/stream.py`
- Test: `tests/test_ui_stream.py`

**Interfaces:**
- Consumes: `protocol.PROTOCOL`, `protocol.CATALOG_VERSION`.
- Produces: `stream.supports(config, catalog_ids: set[str] | frozenset[str]) -> bool` — signature changed from a single `str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ui_stream.py`:

```python
def _config(ids: list[str]) -> dict:
    return {
        "configurable": {
            "assistant_ui": {
                "protocol": "assistant-ui/1.0",
                "catalogVersion": "1",
                "catalogIds": ids,
            }
        }
    }


def test_supports_requires_every_requested_type():
    assert stream.supports(_config(["card", "text"]), {"card", "text"}) is True
    assert stream.supports(_config(["card", "text"]), {"card", "numberField"}) is False


def test_an_older_client_still_gets_the_types_it_declared():
    """Per-type gating rather than a version check is what keeps a phone on
    an old build useful: it can genuinely render a text/card summary, and is
    refused only the trees containing inputs."""
    old = _config(["column", "row", "card", "text", "badge"])
    assert stream.supports(old, {"card", "text"}) is True
    assert stream.supports(old, {"card", "textField"}) is False


def test_an_empty_request_is_supported_by_any_declaring_client():
    """A tree of zero components is degenerate but not a capability
    failure - `validate_operation` is what rejects it, with a diagnostic."""
    assert stream.supports(_config(["card"]), set()) is True


def test_an_undeclared_client_supports_nothing():
    assert stream.supports(None, {"card"}) is False
    assert stream.supports({}, set()) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ui_stream.py -v`
Expected: FAIL — `supports` compares a set against a list membership test and returns `False` for the passing cases.

- [ ] **Step 3: Widen the signature**

In `src/eve/ui/stream.py`, replace `supports`:

```python
def supports(
    config: RunnableConfig | None, catalog_ids: set[str] | frozenset[str]
) -> bool:
    """Whether the client declared EVERY id in `catalog_ids`. Fails CLOSED.

    A set rather than one id, because a model-authored tree uses many types
    at once and the gate has to be all-or-nothing: a surface is emitted whole
    or not at all, and there is no partial render to fall back to.

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
    if not isinstance(ids, list):
        return False
    return set(catalog_ids).issubset(ids)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ui_stream.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/eve/ui/stream.py tests/test_ui_stream.py
git commit -m "feat(ui): gate surfaces on every type in the tree, not one id

Refs EVE-22"
```

---

## Task 5: The `show_surface` tool (server)

**Repo:** `eve-ai`

**Files:**
- Modify: `src/eve/ui/tools.py`
- Modify: `src/eve/graph.py`
- Test: `tests/test_ui_tools.py` (recreate)

**Interfaces:**
- Consumes: `surface.new_surface_id`, `surface.build_create`, `surface.component_types`, `stream.supports`, `stream.emit`, `protocol.validate_operation`, `protocol._ALLOWED_PROPERTIES`.
- Produces: `tools.show_surface` — a LangChain tool with `response_format="content_and_artifact"`, returning `tuple[str, dict | None]`; `tools.schema_hint(types: set[str]) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ui_tools.py`:

```python
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
    content, artifact = await tools.show_surface.ainvoke(
        {"components": TRACKER}, config=CONFIG
    )
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
    content, artifact = await tools.show_surface.ainvoke(
        {"components": bad}, config=CONFIG
    )
    assert artifact is None
    assert written == []
    assert "component-schema" in content
    assert "stateKey" in content
    assert "numberField" in content


async def test_an_old_client_is_refused_only_the_types_it_lacks(written):
    content, artifact = await tools.show_surface.ainvoke(
        {"components": TRACKER}, config=OLD_CLIENT
    )
    assert artifact is None
    assert written == []
    assert "numberField" in content

    plain = [{"id": "c1", "type": "text", "properties": {"text": "Hello"}}]
    _, artifact = await tools.show_surface.ainvoke(
        {"components": plain}, config=OLD_CLIENT
    )
    assert artifact is not None


async def test_a_client_that_declared_nothing_gets_words(written):
    content, artifact = await tools.show_surface.ainvoke(
        {"components": TRACKER}, config={}
    )
    assert artifact is None
    assert written == []
    assert "cannot" in content.lower() or "can't" in content.lower()


async def test_a_rejected_emission_never_returns_an_artifact(monkeypatch):
    """`stream.emit` returns False outside a runnable context. Returning the
    artifact anyway would make `persist_ui` write a frame for a card the
    member never saw."""
    monkeypatch.setattr(tools.stream, "emit", lambda operation: False)
    content, artifact = await tools.show_surface.ainvoke(
        {"components": TRACKER}, config=CONFIG
    )
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
    must not raise trying to describe it."""
    assert tools.schema_hint({"nonsense"}) == ""


def test_the_docstring_carries_no_property_table():
    """The catalog lives in `skills/build-a-ui/SKILL.md`, retrieved on
    demand. A table here would be in context on every turn a capable client
    is connected, whether or not a UI is wanted."""
    doc = tools.show_surface.description
    assert "stateKey" not in doc
    assert "numberField" not in doc
    # Generous, but it fails loudly if someone pastes the table back in.
    assert len(doc) < 900
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ui_tools.py -v`
Expected: FAIL with `AttributeError: module 'eve.ui.tools' has no attribute 'show_surface'`

- [ ] **Step 3: Write the tool**

Replace `src/eve/ui/tools.py` with:

```python
"""The one tool the model gets: put a surface it composed on screen.

The asymmetry ADR 0014 drew - model decides WHETHER, server decides WHAT -
is gone, and ADR 0017 says why. What replaces it is narrower: the model
authors STRUCTURE, the server owns the envelope and the validation, and a
rejection comes back as a diagnostic the model can act on rather than the
silent drop the client would otherwise perform.
"""

from __future__ import annotations

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


@tool(response_format="content_and_artifact")
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ui_tools.py -v`
Expected: PASS

- [ ] **Step 5: Bind the tool in the graph**

In `src/eve/graph.py`, add the import:

```python
from eve.ui.tools import show_surface
```

and in `_static_tools`, before the final `return tools`:

```python
    # Not a setting but the connected client's own capability declaration
    # (`config.configurable.assistant_ui`). A second setting for the same
    # question would be a second thing to keep in step, and a surface emitted
    # at a client that cannot render it goes into that thread's transcript
    # permanently. Bound whenever the client declares anything at all; the
    # per-type gate inside the tool is what refuses an individual tree.
    if ui_stream.capabilities(config) is not None:
        tools.append(show_surface)
```

Restore the sentence in `_static_tools`'s docstring, replacing "three switches gate three tools" with "four switches gate four tools".

- [ ] **Step 6: Write the binding test**

Append to `tests/test_graph.py`:

```python
def test_show_surface_is_bound_only_for_a_declaring_client():
    from eve.graph import _static_tools

    names = {t.name for t in _static_tools({})}
    assert "show_surface" not in names

    declared = {
        "configurable": {
            "assistant_ui": {
                "protocol": "assistant-ui/1.0",
                "catalogVersion": "1",
                "catalogIds": ["card", "text"],
            }
        }
    }
    assert "show_surface" in {t.name for t in _static_tools(declared)}
```

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/eve/ui/tools.py src/eve/graph.py tests/test_ui_tools.py tests/test_graph.py
git commit -m "feat(ui): add show_surface, the one tool for any model-authored UI

Refs EVE-22"
```

---

## Task 6: The submit round trip (server)

**Repo:** `eve-ai`

**Files:**
- Modify: `src/eve/ui/actions.py`
- Modify: `src/eve/graph.py`
- Test: `tests/test_ui_actions.py`, `tests/test_graph.py`

**Interfaces:**
- Consumes: `protocol.PROTOCOL`, `protocol.ACTION_IDS`, `parse_action`.
- Produces: `actions.ui_submit(state: EveState) -> dict`; `actions.readable_submission(state: dict) -> str`.

- [ ] **Step 1: Write the failing tests**

Replace the placeholder test from Task 1 in `tests/test_ui_actions.py`, and append the tests below. `pytest` and `HumanMessage` are already imported at the top of that file — extend the existing `from eve.ui.actions import ...` line rather than adding a second one:

```python
from eve.ui.actions import parse_action, readable_submission, ui_submit

SUBMIT = {
    "protocol": "assistant-ui/1.0",
    "sessionId": "session-1",
    "surfaceId": "sf-1",
    "actionId": "surface.submit",
    "value": None,
    "data": {},
    "state": {"exercise": "Bench press", "reps": 8, "weight": 185},
}


def test_a_submit_envelope_is_recognised():
    assert parse_action(_encoded(SUBMIT)) == SUBMIT


def test_ordinary_member_speech_is_still_not_an_action():
    assert parse_action("what's the weather like?") is None
    assert parse_action("") is None


def test_a_submission_reads_as_a_sentence():
    assert readable_submission(SUBMIT["state"]) == (
        "I filled in the form — Exercise: Bench press · Reps: 8 · Weight: 185"
    )


def test_an_empty_submission_still_reads_as_something():
    """A form with no inputs, or every field left blank. The model needs a
    turn it can answer, not an empty string - Anthropic's Messages API
    rejects an empty non-final message outright."""
    assert readable_submission({}) == "I submitted the form with nothing filled in."


def test_frame_markers_in_typed_text_are_stripped():
    """A member typing the marker is self-only blast radius, but it should
    not reach the transcript intact: `strip_frames` inverts what
    `append_frame` produces, and a forged marker at the true end of a
    message is the one shape it would act on."""
    sentence = readable_submission({"note": "hi </assistant-ui> there"})
    assert "</assistant-ui>" not in sentence
    assert "<assistant-ui>" not in sentence


async def test_ui_submit_replaces_the_envelope_in_the_transcript():
    """Same id, so `add_messages` REPLACES rather than appends: the raw
    envelope would otherwise render as a user bubble full of JSON on
    reopen."""
    original = HumanMessage(content=_encoded(SUBMIT), id="m-1")
    result = await ui_submit({"messages": [original]})
    replaced = result["messages"][0]
    assert replaced.id == "m-1"
    assert isinstance(replaced, HumanMessage)
    assert "Bench press" in replaced.content
    assert "surfaceId" not in replaced.content


async def test_ui_submit_leaves_ordinary_speech_alone():
    """Unreachable through the router, which only sends a parsed envelope
    here. Defensive, because the alternative is corrupting a real message."""
    original = HumanMessage(content="hello", id="m-1")
    assert await ui_submit({"messages": [original]}) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ui_actions.py -v`
Expected: FAIL with `ImportError: cannot import name 'readable_submission'`

- [ ] **Step 3: Implement the node**

In `src/eve/ui/actions.py`, restore the `HumanMessage` and `EveState` imports and append:

```python
def readable_submission(state: object) -> str:
    """What the raw envelope is replaced with in the transcript.

    A reopened session renders this as the member's own words, and it is also
    what the VOICE model reads as the turn it has to answer - so it has to be
    a sentence, never JSON and never empty. Anthropic's Messages API (the
    proxy's fallback tier) rejects an empty non-final message outright.

    Frame markers are stripped from typed text. A member typing one is
    self-only blast radius, but `strip_frames` inverts exactly what
    `append_frame` produces, and a forged marker at the true end of a message
    is the one shape it would act on.
    """
    if not isinstance(state, dict) or not state:
        return "I submitted the form with nothing filled in."
    parts = []
    for key, value in state.items():
        label = str(key).replace("_", " ").strip().capitalize()
        parts.append(f"{label}: {_clean(value)}")
    return "I filled in the form — " + " · ".join(parts)


def _clean(value: object) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace(protocol.OPENING_MARKER.strip(), "")
        .replace(protocol.CLOSING_MARKER.strip(), "")
        .strip()
    )


async def ui_submit(state: EveState) -> dict:
    """Turn a Save tap into an ordinary turn, then get out of the way.

    No model call here, and no frame: unlike the weather tap this replaces,
    a submit has no predetermined answer. `_route_after_context` sends this
    on to `recall` rather than END, so Eve reads the values as a sentence and
    decides herself where they go - memory, a skill, a tool.

    The envelope's `state` IS trusted, which inverts ADR 0014's rule that the
    envelope is never trusted. There is nothing to re-read: the member's
    typed values are the source of truth. `validate_json_value` still capped
    every string at 2,048 characters on the way in.
    """
    last = state["messages"][-1]
    envelope = parse_action(last.content)
    if envelope is None:
        # Unreachable through `_route_after_context`, which only routes here
        # for an envelope this same function parsed. Defensive, because the
        # alternative is corrupting a real member message.
        return {}
    return {
        "messages": [
            HumanMessage(content=readable_submission(envelope.get("state")), id=last.id)
        ]
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ui_actions.py -v`
Expected: PASS

- [ ] **Step 5: Wire the node into the graph**

In `src/eve/graph.py`, change the import to `from eve.ui.actions import parse_action, ui_submit` and replace `_route_after_context`:

```python
def _route_after_context(state: EveState, config: RunnableConfig) -> str:
    """A Save tap arrives as ordinary user text, because the client re-runs
    the turn with the user message's content replaced by an action envelope.

    It routes to `ui_submit`, which rewrites the envelope into a sentence and
    continues to `recall` - NOT to END, the way the weather tap did. A submit
    has no predetermined answer, and Eve is about to decide where to put
    something, which is exactly when she wants memory context.

    Gated on `ui_stream.capabilities`, not just `parse_action`, so the
    fail-closed constraint holds: an undeclared client's envelope becomes
    ordinary member speech, no frame is emitted, and a normal model answer
    comes back.

    Branching after `load_context` rather than at START is deliberate -
    `load_context` is pure local computation (ADR 0002).
    """
    messages = state["messages"]
    if not messages or not isinstance(messages[-1], HumanMessage):
        return "recall"
    if not parse_action(messages[-1].content):
        return "recall"
    return "ui_submit" if ui_stream.capabilities(config) is not None else "recall"
```

Replace `builder.add_edge("load_context", "recall")` with:

```python
    builder.add_node("ui_submit", ui_submit)
    builder.add_conditional_edges(
        "load_context",
        _route_after_context,
        {"ui_submit": "ui_submit", "recall": "recall"},
    )
    builder.add_edge("ui_submit", "recall")
```

(Place `builder.add_node("ui_submit", ui_submit)` beside the other `add_node` calls.)

- [ ] **Step 6: Write the routing test**

Append to `tests/test_graph.py`:

```python
def test_a_submit_envelope_routes_through_ui_submit():
    import json

    from langchain_core.messages import HumanMessage

    from eve.graph import _route_after_context

    envelope = json.dumps(
        {
            "protocol": "assistant-ui/1.0",
            "sessionId": "s-1",
            "surfaceId": "sf-1",
            "actionId": "surface.submit",
            "state": {"reps": 8},
        }
    )
    declared = {
        "configurable": {
            "assistant_ui": {
                "protocol": "assistant-ui/1.0",
                "catalogVersion": "1",
                "catalogIds": ["card"],
            }
        }
    }
    state = {"messages": [HumanMessage(content=envelope)]}
    assert _route_after_context(state, declared) == "ui_submit"
    assert _route_after_context(state, {}) == "recall"
    assert (
        _route_after_context({"messages": [HumanMessage(content="hi")]}, declared)
        == "recall"
    )
```

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/eve/ui/actions.py src/eve/graph.py tests/test_ui_actions.py tests/test_graph.py
git commit -m "feat(ui): route a Save tap to Eve as an ordinary turn

Refs EVE-22"
```

---

## Task 7: The build-a-ui skill (server)

**Repo:** `eve-ai`

**Files:**
- Create: `skills/build-a-ui/SKILL.md`
- Test: `tests/test_skills_build_a_ui.py` (create)

**Interfaces:**
- Consumes: `protocol._ALLOWED_PROPERTIES`, `protocol.CATALOG_IDS`, `eve.skills.registry.parse_skill_text`.
- Produces: a `SKILL.md` discoverable by `search_skills`.

- [ ] **Step 1: Write the failing drift test**

Create `tests/test_skills_build_a_ui.py`:

```python
"""The skill's property table is a FIFTH copy of the catalog, and the only
one written in prose - no validator can catch it drifting. This test is what
catches it instead."""

from __future__ import annotations

import re
from pathlib import Path

from eve.skills.registry import parse_skill_text
from eve.ui import protocol

SKILL = Path(__file__).resolve().parents[1] / "skills" / "build-a-ui" / "SKILL.md"


def _documented() -> dict[str, set[str]]:
    """Every `- \x60type\x60: prop, prop` line in the skill body."""
    name, _description, body = parse_skill_text(SKILL.read_text(), "build-a-ui")
    assert name == "build-a-ui"
    found = {}
    for line in body.splitlines():
        match = re.match(r"^- `([A-Za-z]+)`: (.+)$", line.strip())
        if not match:
            continue
        kind, properties = match.group(1), match.group(2).strip()
        found[kind] = (
            set() if properties == "no properties"
            else {p.strip(" `") for p in properties.split(",")}
        )
    return found


def test_the_skill_documents_every_component_type():
    assert set(_documented()) == set(protocol.CATALOG_IDS)


def test_every_documented_property_matches_the_validator():
    documented = _documented()
    for kind, properties in documented.items():
        assert properties == set(protocol._ALLOWED_PROPERTIES[kind]), kind


def test_the_skill_has_a_description_for_semantic_ranking():
    """`rank_skills` embeds `description or name`, so an empty description
    would make this skill unfindable for every phrasing but its own slug."""
    _name, description, _body = parse_skill_text(SKILL.read_text(), "build-a-ui")
    assert len(description) > 40
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_skills_build_a_ui.py -v`
Expected: FAIL with `FileNotFoundError`

- [ ] **Step 3: Write the skill**

Create `skills/build-a-ui/SKILL.md`:

```markdown
---
name: build-a-ui
description: How to build a dynamic UI surface on screen for a member - a form, a tracker, a checklist, an input, a summary card, a comparison - and the component catalog show_surface accepts.
---
Call `show_surface(components)` with a tree of typed components. Search for
this skill and gather any data you need in the SAME round, then build.

## When a surface is the right answer

Build one when the member wants to enter, track, compare, or choose
something - a workout to log, a checklist to tick, options side by side.
Answer in prose when the answer is a sentence. A card that restates one fact
is worse than saying it.

Keep it phone-sized: one card, a handful of rows. Nobody scrolls a form on a
phone. If it needs more than about eight components, you are building the
wrong thing - ask a question instead.

## How state works

Inputs write to the surface's local state under the `stateKey` you give them.
Nothing you write reaches Eve until the member taps a button whose `actionId`
is `surface.submit` - that hands the whole local state back to you as a new
turn, and you decide what to do with it (remember it, call a tool, answer).

A button with `setState` instead changes local state on the spot with no
round trip. Values are literal - there is no arithmetic, so a counter cannot
increment itself. Let the member type the number.

Every component needs a unique `id` and a `type`. Layout components take
`children`.

## The catalog

- `column`: no properties
- `row`: no properties
- `list`: no properties
- `divider`: no properties
- `card`: title
- `grid`: columns
- `text`: text
- `icon`: name
- `badge`: label
- `expandable`: expanded, label
- `textField`: label, stateKey
- `numberField`: label, stateKey
- `button`: actionId, actionValue, label, setState
- `segmentedSelection`: actionId, actionValue, options, selected

`grid.columns` is 1-6. `expandable.expanded` is a boolean. A `button` must
have exactly one of `actionId` or `setState` - both is two meanings for one
tap, neither is a control that silently ignores them. The only `actionId` is
`surface.submit`.

## A worked example

A workout tracker: a card titled "Workout", a `textField` for the exercise, a
`numberField` each for reps and weight, and a Save button.

    [{"id": "root", "type": "card", "properties": {"title": "Workout"},
      "children": [
        {"id": "ex", "type": "textField",
         "properties": {"label": "Exercise", "stateKey": "exercise"}},
        {"id": "reps", "type": "numberField",
         "properties": {"label": "Reps", "stateKey": "reps"}},
        {"id": "wt", "type": "numberField",
         "properties": {"label": "Weight", "stateKey": "weight"}},
        {"id": "save", "type": "button",
         "properties": {"label": "Save", "actionId": "surface.submit"}}
      ]}]

## If it comes back rejected

The tool answers with a diagnostic code and the legal properties for the
types you used. Fix the tree and call it again - you do not need to search
for this skill a second time. `component-schema` means a property is not
declared for that type; `component-type` means the type does not exist.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_skills_build_a_ui.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add skills/build-a-ui/SKILL.md tests/test_skills_build_a_ui.py
git commit -m "feat(ui): put the surface catalog in a skill, not the tool docstring

Refs EVE-22"
```

---

## Task 8: Persona guidance, parallel-batch test, and ADR 0017 (server)

**Repo:** `eve-ai`

**Files:**
- Modify: `prompts/eve.md`
- Create: `docs/adr/0017-model-authored-surfaces.md`
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing parallel-batch test**

This pins library behaviour the design depends on: `ToolNode` gathers a round's calls concurrently (`tool_node.py:858`) and `_combine_tool_outputs` merges a `Command`-returning tool with a plain-string one. Append to `tests/test_graph.py`:

```python
async def test_a_command_tool_and_a_plain_tool_batch_in_one_round():
    """`search_skills` returns a Command (it updates `dynamic_tools`); a data
    tool returns a string. Issuing both in one round is what makes
    `[search_skills || ask_home] -> show_surface` two rounds instead of
    three, so the mix is worth pinning - it is library behaviour, not ours."""
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.tools import tool as make_tool
    from langgraph.prebuilt import ToolNode
    from langgraph.types import Command

    @make_tool
    def plain(text: str) -> str:
        """A plain tool."""
        return f"plain:{text}"

    @make_tool
    def commanding(text: str) -> Command:
        """A Command-returning tool."""
        return Command(
            update={
                "messages": [ToolMessage(f"cmd:{text}", tool_call_id="call-2")],
                "dynamic_tools": [],
            }
        )

    node = ToolNode([plain, commanding])
    message = AIMessage(
        content="",
        tool_calls=[
            {"name": "plain", "args": {"text": "a"}, "id": "call-1", "type": "tool_call"},
            {
                "name": "commanding",
                "args": {"text": "b"},
                "id": "call-2",
                "type": "tool_call",
            },
        ],
    )
    result = await node.ainvoke({"messages": [message], "dynamic_tools": []})
    rendered = repr(result)
    assert "plain:a" in rendered
    assert "cmd:b" in rendered
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_graph.py::test_a_command_tool_and_a_plain_tool_batch_in_one_round -v`
Expected: PASS (this pins existing library behaviour rather than driving new code — if it FAILS, stop and report, because the design's round-count argument depends on it)

- [ ] **Step 3: Add the persona guidance**

In `prompts/eve.md`, restore a "What you can put on screen" block where the weather one was, after the "What you care about" list:

```
What you can put on screen:
- When someone wants to enter, track, compare or choose something, build them
  a surface instead of describing one. Search your skills for "build a UI"
  and gather whatever data you need in the same step, then build it.
- One card, a handful of rows. If it needs more than that, ask a question
  instead of building a form nobody will fill in.
- Say one short sentence alongside it. Never read the surface out loud.
- If a surface cannot be shown, answer in words and do not mention that
  anything failed.
```

- [ ] **Step 4: Write ADR 0017**

Create `docs/adr/0017-model-authored-surfaces.md`:

```markdown
# 17. The model authors surface structure; the server owns the envelope

**Status:** Accepted
**Date:** 2026-09-02
**Supersedes:** [ADR 0014](0014-dynamic-ui-is-server-built.md)

## Context

ADR 0014 decided that the model's only decision is WHETHER a surface is the
right answer: it called a no-argument `show_weather`, and `eve.ui.weather`
assembled everything in the card server-side. Every new kind of surface was
therefore a new `show_{thing}` tool plus a new builder, authored by a human.
A member who wants a workout tracker cannot have one, and never could.

## Decision

One tool, `show_surface(components)`. The model authors the component tree.
The server mints the surface id, sets `catalogId`, validates against the
client mirror, and returns the diagnostic when it rejects.

ADR 0014 gave three reasons not to let the model emit surface JSON. They are
not equally durable:

1. **"The client rejects silently."** Solved. That was an objection about a
   missing feedback channel. `eve.ui.protocol` already mirrors the client's
   validator; returning its code as the tool result turns the invisible drop
   into a correction the model acts on inside the existing tool loop. The
   rejection carries the legal properties for the types the model actually
   used, so the retry needs no second skills lookup - `rank_skills` is
   semantic and can miss, and correctness must not depend on it twice.
2. **"A model asked for a forecast will produce one whether or not it has
   data."** Accepted as a trade. It was always about DATA, not structure, and
   it does not apply to input surfaces: a workout tracker has nothing to
   invent because the member types every value.
3. **"The surface JSON would occupy the model's own context."** Mostly
   resolved, by moving the catalog into `skills/build-a-ui/SKILL.md` where
   `search_skills` retrieves it on demand. Nothing UI-specific sits in the
   tool list on turns that build no UI. What remains is the tree the model
   authored, in that turn's tool call; `strip_frames` cannot help, because
   it is a tool argument rather than message content.

## Consequences

**There is no UI-specific data path.** A `read_data(source)` tool was
designed and cut: the only surface needing it was the forecast strip, which
this ADR deletes, and it would have added a top-level tool AND a permission
registry - every existing path to eve-tools is gated by a specialist, and an
open `read_data` would route around that. Data reaches a surface through the
tools Eve already has, which are prose. A composed card can therefore
transcribe a number wrong. That is the trade.

**`show_surface` takes no `data`.** Nothing produces `$data.` bindings, so
nothing can hit the failure they carry - an unresolvable binding renders the
WHOLE-surface fallback, not a partial tree. The resolver stays on both sides
as mirror code with no producer, along with patch support, because the client
still implements both and removing them touches four validator copies for no
behavioural gain.

**The 8-surface cap lost the premise that made it safe.** ADR 0014 conceded
that "6 rounds is below 8 surfaces" counts ROUNDS and assumes one call per
round, and rested on `show_weather` being "one no-argument tool a model has
no reason to call more than once". `show_surface` is a tool a model has
plausible reason to call more than once, and parallel batching is now
encouraged. Still deferred, on narrower grounds: the catastrophic case is
already prevented, since nine creates in one frame makes the client reject
the WHOLE frame and `persist_ui` trims to eight and says so. What remains is
that the live `custom` stream is uncapped, so a client can briefly render
more surfaces than a reopened transcript shows - cosmetic and transient.

**A counter derived from `state["messages"]` cannot fix that.** Parallel
siblings execute against the same state snapshot, so each reads zero surfaces
already emitted and every one passes. The idiom `_tool_rounds_this_turn` and
`persist_ui` both use does not extend here. Any real fix is run-scoped
mutable state in `eve.ui.stream`, which is a good part of why it is not worth
building for a cosmetic bound.

**The envelope's `state` IS trusted**, inverting ADR 0014's rule that the
envelope is never trusted and is re-read from Home Assistant instead. There
is nothing to re-read: the member's typed values are the source of truth.
`validate_json_value` still caps every string, and `readable_submission`
strips frame markers from typed text.

**`ui_action` is gone, and with it the codebase's only raising node.** ADR
0014's caveat that "`ui_action` raises where the rest of Eve returns a
string" is retired rather than given a second instance. A submit routes to
`ui_submit`, which rewrites the envelope into a sentence and continues to
`recall` - a submit has no predetermined answer, and Eve is about to decide
where to put something, which is when she wants memory context.

**The catalog now has five hand-synced copies**, up from four: the server
validator, three client layers, and the skill. The skill is prose, so no
validator can catch it drifting; `tests/test_skills_build_a_ui.py` asserts it
against `protocol._ALLOWED_PROPERTIES` instead.

**Old threads lose their weather cards.** Frames from before this change stay
in the transcript and now render the neutral fallback, because
`weather_surface.dart` is deleted.
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add prompts/eve.md docs/adr/0017-model-authored-surfaces.md tests/test_graph.py
git commit -m "docs(ui): record ADR 0017, model-authored surface structure

Refs EVE-22"
```

---

## Task 9: Delete the weather surface (client)

**Repo:** `flutter-open-assistant`

**Files:**
- Delete: `lib/ui/features/chat/dynamic_ui/weather_surface.dart`
- Delete: `test/ui/features/chat/dynamic_ui/weather_surface_test.dart`
- Delete: `test/fixtures/dynamic_ui/weather_create.jsonl`, `weather_patch_daily.jsonl`
- Modify: `lib/ui/features/chat/dynamic_ui/dynamic_surface_renderer.dart`
- Modify: `lib/ui/features/chat/dynamic_ui/dynamic_surface_catalog.dart`
- Modify: `lib/data/services/agent/dynamic_surface_protocol.dart`
- Modify: `lib/domain/models/dynamic_ui/dynamic_ui_capabilities.dart`
- Modify: `lib/data/services/agent/mock_agent_service.dart`
- Modify: `pubspec.yaml:129-133`
- Delete: `integration_test/dynamic_weather_flow_test.dart`

**Interfaces:**
- Consumes: nothing.
- Produces: three client catalog lists without `'weather'`; `DynamicSurfaceProtocol` with no `weather.rangeChanged`.

- [ ] **Step 1: Delete the files**

```bash
cd /Users/nchalifo/GitHub/open-assistant/flutter-open-assistant
git rm lib/ui/features/chat/dynamic_ui/weather_surface.dart \
       test/ui/features/chat/dynamic_ui/weather_surface_test.dart \
       test/fixtures/dynamic_ui/weather_create.jsonl \
       test/fixtures/dynamic_ui/weather_patch_daily.jsonl \
       integration_test/dynamic_weather_flow_test.dart
```

- [ ] **Step 2: Strip weather from the three catalog lists**

Remove `'weather',` from the `ids` set in `dynamic_surface_catalog.dart`, from `catalogIds` in `dynamic_ui_capabilities.dart`, and from the equivalent id set in `dynamic_surface_protocol.dart`. Update the "identical closed ID list is declared independently in three places" comment to say the server's `eve.ui.protocol` is a fourth and `skills/build-a-ui/SKILL.md` a fifth.

- [ ] **Step 3: Strip weather from the renderer**

In `dynamic_surface_renderer.dart`, delete the `import 'weather_surface.dart';` line, the whole `_buildWeather` method, and this branch from `build`:

```dart
    if (definition.catalogId == 'weather') {
      return _buildWeather(definition, dispatch);
    }
```

- [ ] **Step 4: Strip the weather action and property schemas**

In `dynamic_surface_protocol.dart`, remove the `'weather'` entry from the per-type property map and delete the `'location'`, `'condition'` and `'temperature'` property validators along with any now-unused helpers they were the only callers of.

Leave `_validateActionId` in place for now; Task 10 rewrites it. Change its body to reject everything, so nothing dispatches between tasks:

```dart
  // Emptied with the weather surface. Task 10 refills it with
  // `surface.submit`.
  static String? _validateActionId(Object? value) => 'action-schema';
```

- [ ] **Step 5: Update the mock service and bundled fixtures**

In `mock_agent_service.dart`, replace the weather surface fixture with a plain card so the mock still exercises the generic path:

```dart
    catalogId: 'card',
    components: const [
      DynamicComponent(
        id: 'c1',
        type: 'text',
        properties: {'text': 'Mock surface'},
      ),
    ],
    localState: const {},
```

In `pubspec.yaml`, remove the two deleted `.jsonl` asset lines, leaving `weather_malformed.txt` (rename it to `malformed.txt` and update its referencing test, since it is no longer weather-specific).

- [ ] **Step 6: Run the tests**

Run: `flutter test`
Expected: PASS, after deleting or updating any remaining test that references `weather`

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(ui)!: delete the bespoke weather surface

Threads containing weather cards from before this change now render the
neutral fallback; the frames stay in the transcript.

Refs EVE-22"
```

---

## Task 10: Protocol validation for the new contract (client)

**Repo:** `flutter-open-assistant`

**Files:**
- Modify: `lib/data/services/agent/dynamic_surface_protocol.dart`
- Modify: `lib/ui/features/chat/dynamic_ui/dynamic_surface_catalog.dart`
- Modify: `lib/domain/models/dynamic_ui/dynamic_ui_capabilities.dart`
- Test: `test/data/services/agent/dynamic_surface_protocol_test.dart`

**Interfaces:**
- Consumes: Task 9's stripped catalog.
- Produces: three client lists containing `'textField'` and `'numberField'`; `DynamicSurfaceProtocol` accepting `stateKey`, `setState`, and `surface.submit`, and enforcing the button exactly-one-of rule.

- [ ] **Step 1: Write the failing tests**

Append to `test/data/services/agent/dynamic_surface_protocol_test.dart`:

```dart
  Map<String, Object?> create(List<Map<String, Object?>> components) => {
    'protocol': 'assistant-ui/1.0',
    'op': 'create',
    'surface': {
      'surfaceId': 'sf-1',
      'catalogId': 'column',
      'catalogVersion': '1',
      'components': components,
    },
  };

  test('a text field declares a state key and a label', () {
    expect(
      DynamicSurfaceProtocol.validateOperation(create([
        {
          'id': 'c1',
          'type': 'textField',
          'properties': {'stateKey': 'exercise', 'label': 'Exercise'},
        }
      ])),
      isNull,
    );
  });

  test('a state key may not be a binding', () {
    expect(
      DynamicSurfaceProtocol.validateOperation(create([
        {
          'id': 'c1',
          'type': 'textField',
          'properties': {'stateKey': r'$data.reps', 'label': 'Reps'},
        }
      ])),
      'component-schema',
    );
  });

  test('a button may set local state', () {
    expect(
      DynamicSurfaceProtocol.validateOperation(create([
        {
          'id': 'c1',
          'type': 'button',
          'properties': {
            'label': 'Clear',
            'setState': {'reps': 0},
          },
        }
      ])),
      isNull,
    );
  });

  test('a button may submit', () {
    expect(
      DynamicSurfaceProtocol.validateOperation(create([
        {
          'id': 'c1',
          'type': 'button',
          'properties': {'label': 'Save', 'actionId': 'surface.submit'},
        }
      ])),
      isNull,
    );
  });

  test('a button may not do both or neither', () {
    expect(
      DynamicSurfaceProtocol.validateOperation(create([
        {
          'id': 'c1',
          'type': 'button',
          'properties': {
            'label': 'Save',
            'actionId': 'surface.submit',
            'setState': {'done': true},
          },
        }
      ])),
      'component-schema',
    );
    expect(
      DynamicSurfaceProtocol.validateOperation(create([
        {
          'id': 'c1',
          'type': 'button',
          'properties': {'label': 'Save'},
        }
      ])),
      'component-schema',
    );
  });

  test('weather.rangeChanged is no longer an action', () {
    expect(
      DynamicSurfaceProtocol.validateOperation(create([
        {
          'id': 'c1',
          'type': 'button',
          'properties': {'label': 'Go', 'actionId': 'weather.rangeChanged'},
        }
      ])),
      'action-schema',
    );
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `flutter test test/data/services/agent/dynamic_surface_protocol_test.dart`
Expected: FAIL — `component-type` for the input cases, `action-schema` for the submit case

- [ ] **Step 3: Implement the validator changes**

Add `'textField'` and `'numberField'` to the id set in all three client files (`DynamicSurfaceProtocol`, `DynamicSurfaceCatalog.ids`, `DynamicUiCapabilities.v1.catalogIds`).

In `dynamic_surface_protocol.dart`, add the property entries beside the existing `'button' => {...}` line:

```dart
      'button' => {'label', 'actionId', 'actionValue', 'setState'},
      'textField' => {'stateKey', 'label'},
      'numberField' => {'stateKey', 'label'},
```

Add the two property validators to the `switch` on `entry.key`:

```dart
        'stateKey' => _validateStateKey(entry.value),
        'setState' => _validateSetState(entry.value),
```

and define them:

```dart
  /// A literal key, never a binding: it names a localState slot to WRITE,
  /// and `$data.` resolves against read-only surface data.
  static String? _validateStateKey(Object? value) =>
      value is String && value.isNotEmpty && !value.startsWith(r'$')
          ? validateJsonValue(value)
          : 'component-schema';

  static String? _validateSetState(Object? value) => value is Map
      ? validateJsonValue(Map<String, Object?>.from(value))
      : 'component-schema';
```

Replace `_validateActionId`:

```dart
  static const _actionIds = <String>{'surface.submit'};

  static String? _validateActionId(Object? value) =>
      _actionIds.contains(value) ? null : 'action-schema';
```

After the per-property loop in the component-properties validator, add the button rule — this is the one check needing the component type, which that function already has:

```dart
    if (type == 'button') {
      // Exactly one, never both and never neither. Both would be one tap
      // with two meanings in an order the protocol never states; neither
      // renders a live control that silently ignores taps.
      final declared = (properties.containsKey('actionId') ? 1 : 0) +
          (properties.containsKey('setState') ? 1 : 0);
      if (declared != 1) return 'component-schema';
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `flutter test test/data/services/agent/dynamic_surface_protocol_test.dart`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `flutter test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(ui): validate input components, setState, and surface.submit

Refs EVE-22"
```

---

## Task 11: Client-mutable local state

The largest client change. `localState` has only ever been written by the server, via patches.

**Repo:** `flutter-open-assistant`

**Files:**
- Modify: `lib/domain/use_cases/assistant_session_use_case.dart`
- Modify: `lib/ui/features/chat/views/chat_message.dart:251`
- Modify: `lib/ui/features/chat/dynamic_ui/dynamic_surface_renderer.dart`
- Test: `test/domain/use_cases/assistant_session_use_case_test.dart`

**Interfaces:**
- Consumes: `DynamicSurfaceCache.upsert`, `_findLiveSurface`.
- Produces: `AssistantSessionUseCase.mergeLocalState(String surfaceId, Map<String, Object?> patch) -> Future<void>`; `DynamicSurfaceRenderer` constructor parameter `required Future<void> Function(Map<String, Object?> patch) onLocalStateChanged`.

- [ ] **Step 1: Write the failing tests**

Append to `test/domain/use_cases/assistant_session_use_case_test.dart`:

```dart
  test('merging local state updates the live surface without a round trip', () async {
    final useCase = buildUseCase();
    await seedSurfaceWithId(useCase, 'sf-1');

    await useCase.mergeLocalState('sf-1', {'reps': 8});

    final part = findSurface(useCase, 'sf-1')!;
    expect(part.definition.localState['reps'], 8);
    expect(fakeAgentService.runCount, 0);
  });

  test('merging preserves keys it does not name', () async {
    final useCase = buildUseCase();
    await seedSurfaceWithId(useCase, 'sf-1');

    await useCase.mergeLocalState('sf-1', {'reps': 8});
    await useCase.mergeLocalState('sf-1', {'weight': 185});

    final part = findSurface(useCase, 'sf-1')!;
    expect(part.definition.localState['reps'], 8);
    expect(part.definition.localState['weight'], 185);
  });

  test('merged local state is written through to the cache', () async {
    final useCase = buildUseCase();
    await seedSurfaceWithId(useCase, 'sf-1');

    await useCase.mergeLocalState('sf-1', {'reps': 8});

    final cached = await cache.readSession(remoteKey);
    expect(cached.single.localState['reps'], 8);
  });

  test('merging an unknown surface is a no-op, not a crash', () async {
    final useCase = buildUseCase();
    await useCase.mergeLocalState('sf-missing', {'reps': 8});
    expect(findSurface(useCase, 'sf-missing'), isNull);
  });
```

Add the helpers this needs beside the file's existing fixtures — `seedSurfaceWithId` creates a surface part through the same path a `create` frame does, and `findSurface` mirrors `_findLiveSurface`. Follow the patterns already in the file for building a session and a fake agent service.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `flutter test test/domain/use_cases/assistant_session_use_case_test.dart`
Expected: FAIL — `mergeLocalState` is not defined

- [ ] **Step 3: Implement the merge**

In `assistant_session_use_case.dart`, beside `submitUiAction`:

```dart
  /// Merges [patch] into one live surface's local state and writes it
  /// through to the cache. No round trip: this is the client's own
  /// presentation memory, and typing must not cost a turn.
  ///
  /// The cache write is what makes typed-but-unsubmitted values survive a
  /// relaunch. `_mergeCachedLocalState` already restores `localState` and
  /// only `localState` on reopen, so this rides machinery that exists for
  /// exactly this.
  Future<void> mergeLocalState(
    String surfaceId,
    Map<String, Object?> patch,
  ) async {
    final session = _sessionRepo.currentSession;
    final part = _findLiveSurface(session, surfaceId);
    if (part == null) {
      log.warning(
        'AssistantSessionUseCase: mergeLocalState ignored — surface not found',
      );
      return;
    }
    final merged = <String, Object?>{...part.definition.localState, ...patch};
    part.definition = part.definition.copyWith(localState: merged);
    _notify();
    final remoteKey = session.remoteSessionKey;
    if (remoteKey != null) {
      await _surfaceCache.upsert(remoteKey, part.definition);
    }
  }
```

If `DynamicSurfaceDefinition` has no `copyWith`, add one that carries every field through — `surfaceId`, `catalogId`, `catalogVersion`, `components`, `data`, `localState` — defaulting each to the current value.

- [ ] **Step 4: Thread the callback to the renderer**

In `dynamic_surface_renderer.dart`, add the constructor parameter and field:

```dart
    required this.onLocalStateChanged,
```

```dart
  final Future<void> Function(Map<String, Object?> patch) onLocalStateChanged;
```

In `chat_message.dart:251`, pass it at the construction site:

```dart
          onLocalStateChanged: (patch) =>
              useCase.mergeLocalState(part.definition.surfaceId, patch),
```

(Match the surrounding code's name for the use case; `onRemoteAction` on the same call shows how it reaches one.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `flutter test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(ui): let the client write its own surface local state

Refs EVE-22"
```

---

## Task 12: The input components (client)

**Repo:** `flutter-open-assistant`

**Files:**
- Modify: `lib/ui/features/chat/dynamic_ui/dynamic_surface_renderer.dart`
- Test: `test/ui/features/chat/dynamic_ui/dynamic_surface_renderer_test.dart`

**Interfaces:**
- Consumes: `onLocalStateChanged` from Task 11.
- Produces: renderer cases for `'textField'` and `'numberField'`.

- [ ] **Step 1: Write the failing tests**

Append to `test/ui/features/chat/dynamic_ui/dynamic_surface_renderer_test.dart`:

```dart
  testWidgets('a text field shows its label and its current value', (tester) async {
    await pumpSurface(
      tester,
      components: const [
        DynamicComponent(
          id: 'c1',
          type: 'textField',
          properties: {'stateKey': 'exercise', 'label': 'Exercise'},
        ),
      ],
      localState: const {'exercise': 'Bench press'},
    );

    expect(find.text('Exercise'), findsOneWidget);
    expect(find.text('Bench press'), findsOneWidget);
  });

  testWidgets('typing reports a local state patch and no remote action', (tester) async {
    final patches = <Map<String, Object?>>[];
    await pumpSurface(
      tester,
      components: const [
        DynamicComponent(
          id: 'c1',
          type: 'textField',
          properties: {'stateKey': 'exercise', 'label': 'Exercise'},
        ),
      ],
      onLocalStateChanged: patches.add,
    );

    await tester.enterText(find.byType(TextField), 'Squat');
    await tester.pump();

    expect(patches, [
      {'exercise': 'Squat'}
    ]);
    expect(remoteActions, isEmpty);
  });

  testWidgets('a number field reports a num, not a string', (tester) async {
    final patches = <Map<String, Object?>>[];
    await pumpSurface(
      tester,
      components: const [
        DynamicComponent(
          id: 'c1',
          type: 'numberField',
          properties: {'stateKey': 'reps', 'label': 'Reps'},
        ),
      ],
      onLocalStateChanged: patches.add,
    );

    await tester.enterText(find.byType(TextField), '8');
    await tester.pump();

    expect(patches.single['reps'], 8);
  });

  testWidgets('an unparseable number reports null rather than a string', (tester) async {
    /// Eve reads this back as the turn she has to answer. A string where a
    /// number belongs would have her save "8x" as a rep count.
    final patches = <Map<String, Object?>>[];
    await pumpSurface(
      tester,
      components: const [
        DynamicComponent(
          id: 'c1',
          type: 'numberField',
          properties: {'stateKey': 'reps', 'label': 'Reps'},
        ),
      ],
      onLocalStateChanged: patches.add,
    );

    await tester.enterText(find.byType(TextField), '8x');
    await tester.pump();

    expect(patches.single['reps'], isNull);
  });

  testWidgets('a field missing its state key renders the fallback', (tester) async {
    await pumpSurface(
      tester,
      components: const [
        DynamicComponent(
          id: 'c1',
          type: 'textField',
          properties: {'label': 'Exercise'},
        ),
      ],
    );

    expect(find.text('This content can’t be shown'), findsOneWidget);
  });
```

Extend the file's existing `pumpSurface` helper with optional `localState` and `onLocalStateChanged` parameters, defaulting to `const {}` and a no-op.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `flutter test test/ui/features/chat/dynamic_ui/dynamic_surface_renderer_test.dart`
Expected: FAIL — the fallback renders, because `_build` returns `null` for an unregistered type

- [ ] **Step 3: Implement the builders**

The free `_build` function and its helpers need the local-state callback and the surface's current local state. Add two parameters to `_build` and thread them through every recursive call site (`_buildChildren`, `_buildCard`, `_buildGrid`, `_buildExpandable`), mirroring how `data` and `dispatch` are already threaded.

Add the cases to the `switch`:

```dart
    case 'textField':
      return _buildField(context, component, localState, setLocal, number: false);
    case 'numberField':
      return _buildField(context, component, localState, setLocal, number: true);
```

and the builder:

```dart
/// One input. Writes to [localState] under its `stateKey` and never
/// dispatches — typing must not cost a turn.
Widget? _buildField(
  BuildContext context,
  DynamicComponent component,
  Map<String, Object?> localState,
  void Function(Map<String, Object?> patch) setLocal, {
  required bool number,
}) {
  final key = component.properties['stateKey'];
  if (key is! String || key.isEmpty) return null;
  final label = component.properties['label']?.toString() ?? '';
  final current = localState[key];
  return _DynamicField(
    // Keyed on the state key so Flutter keeps one controller per field
    // across rebuilds; without it, a rebuild mid-typing resets the cursor.
    key: ValueKey('field:$key'),
    label: label,
    initialValue: current?.toString() ?? '',
    number: number,
    onChanged: (text) => setLocal({
      key: number ? num.tryParse(text) : text,
    }),
  );
}
```

Add a `_DynamicField` `StatefulWidget` beside `_Expandable`, holding a `TextEditingController` initialised from `initialValue`, disposing it, rendering the label above a `TextField` with `keyboardType: number ? const TextInputType.numberWithOptions(decimal: true) : TextInputType.text`, and calling `onChanged`. Follow `_Expandable`'s existing structure and `context.colors` / `context.type` styling.

In `build`, pass `definition.localState` and a setter wrapping `onLocalStateChanged` into the top-level `_build` calls.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `flutter test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(ui): render textField and numberField into local state

Refs EVE-22"
```

---

## Task 13: Local buttons and local selection (client)

**Repo:** `flutter-open-assistant`

**Files:**
- Modify: `lib/ui/features/chat/dynamic_ui/dynamic_surface_renderer.dart`
- Test: `test/ui/features/chat/dynamic_ui/dynamic_surface_renderer_test.dart`

**Interfaces:**
- Consumes: the `setLocal` parameter from Task 12.
- Produces: `setState` handling on `'button'`; local mode on `'segmentedSelection'`.

- [ ] **Step 1: Write the failing tests**

Append to `test/ui/features/chat/dynamic_ui/dynamic_surface_renderer_test.dart`:

```dart
  testWidgets('a setState button merges literals with no round trip', (tester) async {
    final patches = <Map<String, Object?>>[];
    await pumpSurface(
      tester,
      components: const [
        DynamicComponent(
          id: 'c1',
          type: 'button',
          properties: {
            'label': 'Clear',
            'setState': {'reps': 0, 'exercise': ''},
          },
        ),
      ],
      onLocalStateChanged: patches.add,
    );

    await tester.tap(find.text('Clear'));
    await tester.pump();

    expect(patches, [
      {'reps': 0, 'exercise': ''}
    ]);
    expect(remoteActions, isEmpty);
  });

  testWidgets('a submit button dispatches and sets no local state', (tester) async {
    final patches = <Map<String, Object?>>[];
    await pumpSurface(
      tester,
      components: const [
        DynamicComponent(
          id: 'c1',
          type: 'button',
          properties: {'label': 'Save', 'actionId': 'surface.submit'},
        ),
      ],
      onLocalStateChanged: patches.add,
    );

    await tester.tap(find.text('Save'));
    await tester.pump();

    expect(remoteActions.single.actionId, 'surface.submit');
    expect(patches, isEmpty);
  });

  testWidgets('a segmented selection with setState selects locally', (tester) async {
    /// Its only consumer used to be the weather toggle. Without local mode
    /// the component is dead: the one dispatchable action left is
    /// `surface.submit`, which is meaningless on a selector.
    final patches = <Map<String, Object?>>[];
    await pumpSurface(
      tester,
      components: const [
        DynamicComponent(
          id: 'c1',
          type: 'segmentedSelection',
          properties: {
            'options': ['Left', 'Right'],
            'selected': 'Left',
            'setState': {'side': 'Right'},
          },
        ),
      ],
      onLocalStateChanged: patches.add,
    );

    await tester.tap(find.text('Right'));
    await tester.pump();

    expect(patches.single['side'], 'Right');
    expect(remoteActions, isEmpty);
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `flutter test test/ui/features/chat/dynamic_ui/dynamic_surface_renderer_test.dart`
Expected: FAIL — the `setState` button renders with `onPressed: null` and records no patch

- [ ] **Step 3: Implement the branches**

Give `_buildButton` and `_buildSegmentedSelection` the `setLocal` parameter, and thread it from the `switch` in `_build`.

In `_buildButton`, replace the `onPressed` expression:

```dart
  final setState = component.properties['setState'];
  final actionId = component.properties['actionId'];
  // Exactly one is guaranteed by the validator, so this is a total branch;
  // the null fallback is for a frame that reached the renderer some other
  // way, which draws a disabled control rather than crashing.
  final VoidCallback? onPressed = setState is Map
      ? () => setLocal(Map<String, Object?>.from(setState))
      : actionId is String
          ? () => dispatch(actionId, actionValue)
          : null;
  return AppButton(label: label?.toString() ?? '', onPressed: onPressed);
```

Add `'setState'` handling to `_buildSegmentedSelection` the same way — on selecting an option, if `setState` is a `Map`, call `setLocal` with it; otherwise dispatch as it does today.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `flutter test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(ui): add local setState buttons and local segmented selection

Refs EVE-22"
```

---

## Task 14: Carry local state on submit (client)

**Repo:** `flutter-open-assistant`

**Files:**
- Modify: `lib/domain/models/dynamic_ui/ui_action.dart`
- Modify: `lib/data/services/agent/dynamic_surface_protocol.dart`
- Modify: `lib/ui/features/chat/dynamic_ui/dynamic_surface_renderer.dart`
- Test: `test/domain/models/dynamic_ui/dynamic_surface_test.dart`, `test/data/services/agent/dynamic_surface_protocol_test.dart`

**Interfaces:**
- Consumes: `UiAction`.
- Produces: `UiAction.state` (a `Map<String, Object?>`), serialized into the encoded envelope as `state`.

- [ ] **Step 1: Write the failing tests**

Append to `test/data/services/agent/dynamic_surface_protocol_test.dart`:

```dart
  test('an encoded submit carries the surface local state', () {
    final action = UiAction(
      sessionId: 's-1',
      remoteSessionKey: 'remote-1',
      surfaceId: 'sf-1',
      actionId: 'surface.submit',
      value: null,
      data: const {},
      state: const {'exercise': 'Bench press', 'reps': 8},
    );

    final encoded = DynamicSurfaceProtocol.encodeAction(action);
    final body = encoded
        .replaceFirst('<assistant-ui-action>', '')
        .replaceFirst('</assistant-ui-action>', '')
        .trim();
    final decoded = jsonDecode(body) as Map<String, Object?>;

    expect(decoded['actionId'], 'surface.submit');
    expect(decoded['state'], {'exercise': 'Bench press', 'reps': 8});
  });

  test('state round trips through toJson and fromJson', () {
    final action = UiAction(
      sessionId: 's-1',
      remoteSessionKey: null,
      surfaceId: 'sf-1',
      actionId: 'surface.submit',
      data: const {},
      state: const {'reps': 8},
    );
    expect(UiAction.fromJson(action.toJson()).state, {'reps': 8});
  });
```

And to the renderer test file:

```dart
  testWidgets('submitting sends the surface current local state', (tester) async {
    await pumpSurface(
      tester,
      components: const [
        DynamicComponent(
          id: 'c1',
          type: 'button',
          properties: {'label': 'Save', 'actionId': 'surface.submit'},
        ),
      ],
      localState: const {'reps': 8},
    );

    await tester.tap(find.text('Save'));
    await tester.pump();

    expect(remoteActions.single.state, {'reps': 8});
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `flutter test`
Expected: FAIL — `UiAction` has no named parameter `state`

- [ ] **Step 3: Implement the field**

In `ui_action.dart`, add `final Map<String, Object?> state;` and thread it through the factory (`state: freezeJsonMap(state)`, defaulting to `const {}`), the private constructor, `fromJson` (tolerating a missing or non-`Map` `state` as `{}`, since an older provider will not send one), and `toJson` (`'state': copyJsonMap(state)`).

In `dynamic_surface_protocol.dart`'s `encodeAction`, add `'state': action.state,` beside the existing `'actionId'` entry.

In `dynamic_surface_renderer.dart`, the `dispatch` closure in `build` constructs the `UiAction` — add `state: definition.localState,` so a tap carries the values as they are at that moment.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `flutter test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(ui): carry surface local state on submit

Refs EVE-22"
```

---

## Task 15: The end-to-end form flow (client)

Replaces the deleted `dynamic_weather_flow_test.dart`.

**Repo:** `flutter-open-assistant`

**Files:**
- Create: `integration_test/dynamic_form_flow_test.dart`
- Create: `test/fixtures/dynamic_ui/form_create.jsonl`
- Modify: `pubspec.yaml` (bundle the new fixture)

**Interfaces:**
- Consumes: everything from Tasks 9-14.
- Produces: nothing.

- [ ] **Step 1: Write the fixture**

Create `test/fixtures/dynamic_ui/form_create.jsonl` — one JSON object on one line:

```json
{"protocol":"assistant-ui/1.0","op":"create","surface":{"surfaceId":"sf-1","catalogId":"column","catalogVersion":"1","components":[{"id":"root","type":"card","properties":{"title":"Workout"},"children":[{"id":"ex","type":"textField","properties":{"label":"Exercise","stateKey":"exercise"}},{"id":"reps","type":"numberField","properties":{"label":"Reps","stateKey":"reps"}},{"id":"save","type":"button","properties":{"label":"Save","actionId":"surface.submit"}}]}],"data":{},"localState":{}}}
```

Add it to `pubspec.yaml`'s asset list beside the existing fixtures.

- [ ] **Step 2: Write the failing test**

Create `integration_test/dynamic_form_flow_test.dart`, following the structure of the deleted weather flow test (recover it with `git show HEAD~N:integration_test/dynamic_weather_flow_test.dart` for the harness setup — fake agent service, session bootstrap, frame injection):

```dart
  testWidgets('a member fills in a model-built form and submits it', (tester) async {
    await bootstrapApp(tester);
    await emitFrame(tester, fixture: 'form_create.jsonl');

    expect(find.text('Workout'), findsOneWidget);
    expect(find.text('Exercise'), findsOneWidget);

    await tester.enterText(find.byType(TextField).first, 'Bench press');
    await tester.pump();
    await tester.enterText(find.byType(TextField).last, '8');
    await tester.pump();

    // No round trip for typing.
    expect(fakeAgentService.runCount, 1);

    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();

    expect(fakeAgentService.runCount, 2);
    final sent = fakeAgentService.lastAction!;
    expect(sent.actionId, 'surface.submit');
    expect(sent.state, {'exercise': 'Bench press', 'reps': 8});
  });

  testWidgets('typed values survive a relaunch', (tester) async {
    await bootstrapApp(tester);
    await emitFrame(tester, fixture: 'form_create.jsonl');

    await tester.enterText(find.byType(TextField).first, 'Bench press');
    await tester.pump();

    await relaunchApp(tester);

    expect(find.text('Bench press'), findsOneWidget);
  });
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `flutter test integration_test/dynamic_form_flow_test.dart`
Expected: FAIL until the fixture is bundled and the harness helpers are adapted

- [ ] **Step 4: Adapt the harness until it passes**

Rename the recovered helpers away from weather-specific names. No production code should need changing — if it does, that is a real gap in Tasks 9-14; fix it there and note which task it belonged to.

- [ ] **Step 5: Run the full suite**

Run: `flutter test && flutter test integration_test/dynamic_form_flow_test.dart`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "test(ui): cover the model-built form flow end to end

Refs EVE-22"
```

---

## Verification

After Task 15, from `eve-ai`:

```bash
uv run pytest
```

From `flutter-open-assistant`:

```bash
flutter analyze && flutter test
```

Then, against a real device with the updated client connected to the updated server, walk the definition of done in the spec:

1. Ask Eve for a workout tracker. A card with labelled inputs and a Save button appears.
2. Type into the fields. No spinner, no turn.
3. Force-quit and reopen. The typed values are still there.
4. Tap Save. The transcript shows a readable sentence, not JSON, and Eve does something with the values.
5. Reopen the thread. The card is still rendered, from the persisted frame.
