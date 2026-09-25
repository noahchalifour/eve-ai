"""Assembling a model-authored component tree into a create operation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eve.images.store import ImageRow
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


FULL = "3f2a9c01-0000-4000-8000-000000000001"
V2 = {"configurable": {"catalog_versions": ["1", "2"], "thread_id": "t1",
                       "member": {"sub": "sub-noah"}}}
V1 = {"configurable": {"thread_id": "t1", "member": {"sub": "sub-noah"}}}


def _row(width=600, height=900):
    now = datetime.now(UTC)
    return ImageRow(FULL, "sub-noah", "t1", "immich", "a-1", "image/jpeg", b"",
                    width, height, None, now, now + timedelta(days=1))


def _tree(image_id="3f2a9c01", **extra):
    return [{"id": "c", "type": "card", "properties": {"title": "Today"}, "children": [
        {"id": "i", "type": "image",
         "properties": {"imageId": image_id, "alt": "navy blazer", **extra}}]}]


@pytest.fixture
def resolved(monkeypatch):
    calls = []

    async def fake_resolve(ref, member_sub, thread_id, *, now=None):
        calls.append((ref, member_sub, thread_id))
        return _row() if ref in ("3f2a9c01", FULL) else None

    monkeypatch.setattr(surface.store, "resolve", fake_resolve)
    return calls


async def test_a_v2_run_gets_the_full_id_and_a_filled_aspect(resolved):
    tree = _tree()
    prepared, has_image = await surface.prepare_images(tree, V2)
    image = prepared[0]["children"][0]
    assert has_image is True
    assert image["properties"] == {"imageId": FULL, "alt": "navy blazer", "aspect": "portrait"}
    assert resolved == [("3f2a9c01", "sub-noah", "t1")]
    assert tree[0]["children"][0]["properties"]["imageId"] == "3f2a9c01"  # not mutated


async def test_a_model_supplied_aspect_is_kept(resolved):
    prepared, _ = await surface.prepare_images(_tree(aspect="square"), V2)
    assert prepared[0]["children"][0]["properties"]["aspect"] == "square"


async def test_a_v1_run_downgrades_to_text_without_touching_the_store(resolved):
    prepared, has_image = await surface.prepare_images(_tree(), V1)
    assert has_image is False
    assert prepared[0]["children"][0] == {
        "id": "i", "type": "text", "properties": {"text": "navy blazer"}}
    assert resolved == []


async def test_an_unresolvable_id_becomes_text_and_is_logged(resolved, caplog):
    prepared, has_image = await surface.prepare_images(_tree(image_id="deadbeef"), V2)
    assert has_image is False
    assert prepared[0]["children"][0]["type"] == "text"
    assert "did not resolve" in caplog.text


async def test_a_tree_without_images_is_returned_as_is(resolved):
    prepared, has_image = await surface.prepare_images(COMPONENTS, V2)
    assert (prepared, has_image) == (COMPONENTS, False)


def test_aspect_of_thresholds():
    assert surface.aspect_of(1000, 1000) == "square"
    assert surface.aspect_of(1100, 1000) == "square"
    assert surface.aspect_of(1568, 1176) == "landscape"
    assert surface.aspect_of(1176, 1568) == "portrait"


def test_build_create_stamps_the_version_it_is_given():
    op = surface.build_create("sf-1", COMPONENTS, catalog_version="2")
    assert op["surface"]["catalogVersion"] == "2"
