"""A refresh is sources + template, no model. Pure over an injected registry,
so every partial-failure branch is a unit test."""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from eve.ui import protocol


class P(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _source(name, read, ttl=60, targets=frozenset()):
    from eve.widgets.sources.base import SourceType

    return SourceType(name=name, params=P, fields=frozenset({"v", "items"}), read=read, ttl_seconds=ttl,
                      item_fields={"items": frozenset()}, targets=lambda p: targets)


async def good(ctx, params):
    return {"v": "hello", "items": [{"n": "a"}]}


async def bad(ctx, params):
    raise RuntimeError("upstream down, token=secret")


def _resource(sources, template):
    return {"id": "res-1", "kind": "custom", "title": "T", "revision": 3, "filters": {},
            "recipe": {"version": 2, "sources": sources, "template": template}}


TEMPLATE = [{"id": "root", "type": "card", "properties": {}, "children": [
    {"id": "t", "type": "text", "properties": {"text": "$data.a.v"}, "children": []}]}]


@pytest.fixture
def registry(monkeypatch):
    from eve.widgets import sources

    fake = {"good": _source("good", good, ttl=30, targets=frozenset({"light.kitchen"})), "bad": _source("bad", bad, ttl=10)}
    monkeypatch.setattr(sources.base, "REGISTRY", fake)
    return fake


async def test_snapshot_fills_data_and_is_a_valid_widget_surface(registry):
    from eve.widgets import resolve

    snap = await resolve.snapshot(_resource({"a": {"type": "good"}}, TEMPLATE), "sub-noah")

    assert snap["view"]["data"]["a"]["v"] == "hello"
    assert snap["view"]["data"]["widget"]["title"] == "T"
    assert snap["refreshAfterSeconds"] == 30
    op = {"protocol": protocol.PROTOCOL, "op": "create", "surface": {"surfaceId": "w", "catalogId": "column",
          "catalogVersion": "1", "components": snap["view"]["components"], "data": snap["view"]["data"], "localState": {}}}
    assert protocol.validate_operation(op, widget=True) is None


async def test_a_failed_source_is_partial_and_never_leaks_its_message(registry):
    from eve.widgets import resolve

    snap = await resolve.snapshot(_resource({"a": {"type": "good"}, "b": {"type": "bad"}}, TEMPLATE), "s")

    assert snap["sources"] == {"partial": True, "errors": [{"source": "b", "reason": "unavailable"}]}
    assert "secret" not in str(snap)
    assert snap["refreshAfterSeconds"] == 10


async def test_risk_overrides_are_published_per_target(registry, monkeypatch):
    from eve.widgets import resolve
    from eve.widgets.actions import REGISTRY

    snap = await resolve.snapshot(_resource({"a": {"type": "good"}}, TEMPLATE), "s")
    # light.kitchen is safe for home.toggle == its default, so no override;
    # overrides appear only where risk differs from the action's default.
    assert "home.toggle:light.kitchen" not in snap["actionRisk"]


async def test_a_v1_recipe_still_renders_as_a_chart(monkeypatch):
    from eve.widgets import resolve
    from eve.widgets.sources import series

    async def rows(member_sub, collection, since=None, until=None, limit=500):
        return []

    monkeypatch.setattr(series.record_store, "query", rows)
    resource = {"id": "r", "kind": "chart", "title": "Old", "revision": 1, "filters": {"days": 7},
                "recipe": {"sources": [{"type": "records", "collection": "alpha"}], "metric": {"op": "count"}}}
    snap = await resolve.snapshot(resource, "s")
    assert snap["view"]["data"]["widget"]["days"] == "7"
    assert any(c["type"] == "segmentedSelection" for c in snap["view"]["components"][0]["children"])
