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
