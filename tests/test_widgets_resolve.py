"""The resolver is what makes a refresh cost no model call. It is pure over
injected readers so every partial-failure branch is reachable in a unit test."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

RESOURCE = {
    "id": "res-1",
    "kind": "chart",
    "title": "Alpha",
    "recipe": {
        "sources": [{"type": "records", "collection": "alpha.thing"}],
        "metric": {"op": "count"},
    },
    "filters": {"days": 30},
    "revision": 3,
}


def _records(count: int):
    now = datetime.now(timezone.utc)

    async def read(member_sub, collection, since=None, until=None, limit=500):
        return [
            {"occurred_at": now - timedelta(days=i), "payload": {"weight": 100 + i}}
            for i in range(count)
        ]

    return read


async def _no_health(member_sub, metric, days):
    raise AssertionError("health must not be read for a records-only recipe")


async def test_a_records_recipe_produces_points(monkeypatch):
    from eve.widgets import resolve

    snapshot = await resolve.snapshot(
        RESOURCE, "sub-noah", read_records=_records(3), read_health=_no_health
    )

    assert snapshot["resourceId"] == "res-1"
    assert snapshot["revision"] == 3
    assert snapshot["sources"]["partial"] is False
    assert len(snapshot["view"]["data"]["points"]) == 3


async def test_the_snapshot_view_is_a_valid_surface_tree(monkeypatch):
    """The client validates the whole tree before swapping it in, so an
    invalid tree here is an invisible widget."""
    from eve.ui import protocol
    from eve.widgets import resolve

    snapshot = await resolve.snapshot(
        RESOURCE, "sub-noah", read_records=_records(2), read_health=_no_health
    )

    operation = {
        "protocol": protocol.PROTOCOL,
        "op": "create",
        "surface": {
            "surfaceId": "sf-1",
            "catalogId": "column",
            "catalogVersion": protocol.CATALOG_VERSION,
            "components": snapshot["view"]["components"],
            "data": snapshot["view"]["data"],
            "localState": {},
        },
    }
    # This tree is a WIDGET snapshot, so it validates in the widget-scoped
    # mode, which additionally allows the range control's `widget.setRange`
    # action id - an id the chat protocol deliberately rejects.
    assert protocol.validate_operation(operation, widget=True) is None


async def test_an_empty_collection_says_so_rather_than_charting_nothing():
    from eve.widgets import resolve

    snapshot = await resolve.snapshot(
        RESOURCE, "sub-noah", read_records=_records(0), read_health=_no_health
    )

    rendered = str(snapshot["view"]["components"])
    assert "Nothing recorded" in rendered
    assert snapshot["view"]["data"]["points"] == []


async def test_a_failing_source_is_partial_not_fatal():
    from eve.widgets import resolve

    async def boom(*args, **kwargs):
        raise RuntimeError("upstream exploded")

    snapshot = await resolve.snapshot(
        RESOURCE, "sub-noah", read_records=boom, read_health=_no_health
    )

    assert snapshot["sources"]["partial"] is True
    assert snapshot["sources"]["errors"] == [{"source": "records", "reason": "unavailable"}]


async def test_a_failing_source_never_leaks_the_upstream_message():
    """Public errors are sanitized; the raw string could carry anything."""
    from eve.widgets import resolve

    async def boom(*args, **kwargs):
        raise RuntimeError("psql://user:hunter2@db/eve exploded")

    snapshot = await resolve.snapshot(
        RESOURCE, "sub-noah", read_records=boom, read_health=_no_health
    )

    assert "hunter2" not in str(snapshot)


async def test_the_reader_is_called_with_the_authenticated_member():
    from eve.widgets import resolve

    seen = {}

    async def read(member_sub, collection, since=None, until=None, limit=500):
        seen["member_sub"] = member_sub
        seen["collection"] = collection
        return []

    await resolve.snapshot(
        RESOURCE, "sub-noah", read_records=read, read_health=_no_health
    )

    assert seen == {"member_sub": "sub-noah", "collection": "alpha.thing"}


async def test_a_sum_metric_sums_the_named_field():
    from eve.widgets import resolve

    resource = {**RESOURCE, "recipe": {
        **RESOURCE["recipe"], "metric": {"op": "sum", "field": "weight"},
    }}

    snapshot = await resolve.snapshot(
        resource, "sub-noah", read_records=_records(2), read_health=_no_health
    )

    # 100 + 101, bucketed by day then summed.
    assert sum(p["value"] for p in snapshot["view"]["data"]["points"]) == 201


async def test_a_missing_numeric_field_is_skipped_not_zeroed():
    """An unsupported value is null, never zero (spec)."""
    from eve.widgets import resolve

    async def read(member_sub, collection, since=None, until=None, limit=500):
        return [{"occurred_at": datetime.now(timezone.utc), "payload": {"other": 1}}]

    resource = {**RESOURCE, "recipe": {
        **RESOURCE["recipe"], "metric": {"op": "sum", "field": "weight"},
    }}

    snapshot = await resolve.snapshot(
        resource, "sub-noah", read_records=read, read_health=_no_health
    )

    assert snapshot["view"]["data"]["points"] == []


async def test_a_health_recipe_reads_health():
    from eve.widgets import resolve

    resource = {**RESOURCE, "recipe": {
        "sources": [{"type": "health", "metric": "activity"}],
        "metric": {"op": "sum", "field": "active_calories"},
    }}

    async def read_health(member_sub, metric, days):
        assert member_sub == "sub-noah"
        assert metric == "activity"
        return [{"date": "2026-09-01", "active_calories": 500}]

    snapshot = await resolve.snapshot(
        resource, "sub-noah", read_records=_records(0), read_health=read_health
    )

    assert snapshot["view"]["data"]["points"] == [
        {"label": "2026-09-01", "value": 500, "source": "health"}
    ]


async def test_filters_narrow_the_window():
    from eve.widgets import resolve

    seen = {}

    async def read(member_sub, collection, since=None, until=None, limit=500):
        seen["since"] = since
        return []

    resource = {**RESOURCE, "filters": {"days": 7}}
    await resolve.snapshot(
        resource, "sub-noah", read_records=read, read_health=_no_health
    )

    age = datetime.now(timezone.utc) - seen["since"]
    assert 6 <= age.days <= 7


async def test_the_snapshot_carries_an_inline_range_control():
    """The spec's inline filters: the control ships IN the snapshot, so a
    widget the model authored once stays adjustable without re-authoring."""
    from eve.widgets import resolve

    snapshot = await resolve.snapshot(
        RESOURCE, "sub-noah", read_records=_records(1), read_health=_no_health
    )

    root = snapshot["view"]["components"][0]
    control = next(c for c in root["children"] if c["type"] == "segmentedSelection")
    assert control["properties"]["actionId"] == "widget.setRange"


async def test_the_range_control_shows_the_persisted_selection():
    """It reflects server state, not a local guess that can drift from it."""
    from eve.widgets import resolve

    resource = {**RESOURCE, "filters": {"days": 7}}
    snapshot = await resolve.snapshot(
        resource, "sub-noah", read_records=_records(1), read_health=_no_health
    )

    root = snapshot["view"]["components"][0]
    control = next(c for c in root["children"] if c["type"] == "segmentedSelection")
    assert control["properties"]["selected"] == "7"


async def test_an_empty_snapshot_still_carries_the_control():
    """Otherwise a widget whose collection is empty can never be widened to a
    range that would have found something."""
    from eve.widgets import resolve

    snapshot = await resolve.snapshot(
        RESOURCE, "sub-noah", read_records=_records(0), read_health=_no_health
    )

    root = snapshot["view"]["components"][0]
    assert any(c["type"] == "segmentedSelection" for c in root["children"])