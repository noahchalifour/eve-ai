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
        "surfaceId": "sf-1",
        "catalogId": "column",
        "catalogVersion": "1",
        "components": [
            {
                "id": "c1",
                "type": "card",
                "properties": {"title": "Test"},
                "children": [],
            }
        ],
        "data": {},
        "localState": {},
    }
    surface.update(overrides)
    return {"protocol": protocol.PROTOCOL, "op": "create", "surface": surface}


def test_a_well_formed_create_is_accepted():
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


def test_only_surface_submit_is_a_legal_action_id():
    def button(action_id):
        return [
            {
                "id": "b",
                "type": "button",
                "properties": {"label": "Go", "actionId": action_id},
                "children": [],
            }
        ]

    assert protocol.validate_operation(_surface(components=button("surface.submit"))) is None
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
    data = {"note": "x" * (protocol.MAX_STRING + 1)}
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


def test_strip_frames_is_exactly_inverse_to_frame():
    """`persist.py._with_frame` appends `f"{content}\\n{frame(...)}"`. What
    `strip_frames` removes must be byte-identical to the frame, so the
    original content survives the round trip untouched."""
    operation = {"protocol": protocol.PROTOCOL, "op": "delete", "surfaceId": "wx-1"}
    original = "Nice out there."
    appended = f"{original}\n{protocol.frame([operation])}"

    assert protocol.strip_frames(appended) == original


def test_strip_frames_strips_a_bare_frame_with_nothing_before_it():
    """Some operations are appended as AIMessage(content=frame([op])) directly
    with no prose at all, so content position 0 IS the opening marker. A
    stripper that only recognized a frame preceded by a literal "\\n" would
    leave this shape completely untouched."""
    operation = {"protocol": protocol.PROTOCOL, "op": "delete", "surfaceId": "wx-1"}
    bare = protocol.frame([operation])

    assert protocol.strip_frames(bare) == ""


def test_strip_frames_does_not_span_an_earlier_lookalike_opening_marker():
    """A greedy body reaching from the FIRST opening marker to the LAST
    closing marker would eat everything in between, including real text
    that merely mentions the marker before an actual frame later in the same
    message. Only the true frame - anchored at the end of the string - may
    be removed."""
    operation = {"protocol": protocol.PROTOCOL, "op": "delete", "surfaceId": "wx-1"}
    text = (
        "start\n<assistant-ui>\nnot json\nreal text here\n"
        + protocol.frame([operation])
    )

    assert protocol.strip_frames(text) == "start\n<assistant-ui>\nnot json\nreal text here"


def test_strip_frames_is_a_no_op_on_content_with_no_frame():
    """The near-totality of turns. Must be byte-identical - not just
    "shorter" or "frameless" - after stripping."""
    text = "Just an ordinary answer, no card involved."
    assert protocol.strip_frames(text) == text


def test_strip_frames_does_not_eat_a_passing_mention_of_the_markers():
    """Tolerant-but-conservative reading: only the exact trailing shape
    `_with_frame` produces - a frame anchored at the true end of the string -
    counts. Text that merely contains the words, with no matching close
    marker at the end, is untouched."""
    text = "The docs say a message can contain <assistant-ui> markup."
    assert protocol.strip_frames(text) == text


def test_strip_frames_from_content_drops_the_whole_block_for_list_content():
    """Reasoning-capable models return `content` as a list of typed blocks;
    `_with_frame` appends a WHOLE new block for the frame rather than
    editing an existing one, so stripping it must remove the block, not
    leave an empty `{"type": "text", "text": ""}` behind."""
    operation = {"protocol": protocol.PROTOCOL, "op": "delete", "surfaceId": "wx-1"}
    blocks = [
        {"type": "text", "text": "Nice out."},
        {"type": "text", "text": f"\n{protocol.frame([operation])}"},
    ]

    assert protocol.strip_frames_from_content(blocks) == [
        {"type": "text", "text": "Nice out."}
    ]


def test_strip_frames_from_content_is_a_no_op_on_a_frameless_list():
    blocks = [{"type": "text", "text": "Nice out."}]
    assert protocol.strip_frames_from_content(blocks) == blocks


def test_append_frame_prepends_nothing_to_falsy_content():
    """Frame producers use `append_frame` as a shared builder; falsy content
    (nothing to say ahead of the frame) gets the frame back bare - the shape
    `strip_frames`'s `\\A` branch exists for."""
    operation = {"protocol": protocol.PROTOCOL, "op": "delete", "surfaceId": "wx-1"}
    assert protocol.append_frame("", [operation]) == protocol.frame([operation])
    assert protocol.strip_frames(protocol.append_frame("", [operation])) == ""


def test_append_frame_joins_non_empty_string_content_with_a_newline():
    operation = {"protocol": protocol.PROTOCOL, "op": "delete", "surfaceId": "wx-1"}
    result = protocol.append_frame("Nice out there.", [operation])
    assert result == f"Nice out there.\n{protocol.frame([operation])}"
    assert protocol.strip_frames(result) == "Nice out there."


def test_append_frame_appends_a_new_block_to_list_content():
    """Reasoning-capable models return `content` as a list of typed blocks;
    concatenating a string onto that list would corrupt the message, so this
    always adds a whole new block instead."""
    operation = {"protocol": protocol.PROTOCOL, "op": "delete", "surfaceId": "wx-1"}
    blocks = [{"type": "text", "text": "Nice out."}]
    result = protocol.append_frame(blocks, [operation])
    assert result == [
        {"type": "text", "text": "Nice out."},
        {"type": "text", "text": f"\n{protocol.frame([operation])}"},
    ]
    assert protocol.strip_frames_from_content(result) == blocks


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


def test_a_segmented_selection_may_set_local_state():
    operation = _create(
        [
            {
                "id": "c1",
                "type": "segmentedSelection",
                "properties": {
                    "options": ["option1", "option2"],
                    "selected": "option1",
                    "setState": {"choice": "option1"},
                },
            }
        ]
    )
    assert protocol.validate_operation(operation) is None
