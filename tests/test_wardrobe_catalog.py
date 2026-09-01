"""tests/test_wardrobe_catalog.py"""
import json
from unittest.mock import AsyncMock

from eve.wardrobe import catalog, vision


def _item(name, category="top", **attrs):
    return vision.WardrobeItem(name=name, category=category, **attrs)


def _fake_invoke(album_assets, images=None, truncated=False):
    """Stands in for eve.tools_client.invoke, which returns a JSON STRING."""
    images = images or {}

    async def _invoke(tool, arguments, **kwargs):
        if tool == "immich.album_assets":
            return json.dumps({"assets": album_assets, "truncated": truncated})
        if tool == "immich.asset_image":
            asset_id = arguments["asset_id"]
            return json.dumps(
                images.get(
                    asset_id,
                    {"asset_id": asset_id, "content_type": "image/jpeg", "base64": "aGk="},
                )
            )
        raise AssertionError(f"unexpected tool {tool}")

    return AsyncMock(side_effect=_invoke)


class FakeSpan:
    def __init__(self, spans):
        self._spans = spans

    def set_attribute(self, key, value):
        self._spans.append((key, value))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeTracer:
    def __init__(self, spans):
        self._spans = spans

    def start_as_current_span(self, name):
        self._spans.append(("span.name", name))
        return FakeSpan(self._spans)


def _record_telemetry(monkeypatch):
    spans = []
    monkeypatch.setattr(catalog, "_tracer", FakeTracer(spans))
    return spans


def _patch_common(monkeypatch, *, album="album-1", catalogued=frozenset()):
    monkeypatch.setattr(catalog, "album_for", lambda _sub: album)
    monkeypatch.setattr(
        catalog.store, "catalogued_asset_ids", AsyncMock(return_value=set(catalogued))
    )
    inserted = []

    async def _insert(member_sub, asset_id, items):
        inserted.append((asset_id, items))

    monkeypatch.setattr(catalog.store, "insert_items", AsyncMock(side_effect=_insert))
    monkeypatch.setattr(catalog.store, "delete_assets", AsyncMock())
    return inserted


async def test_sync_catalogues_only_assets_it_has_not_seen(monkeypatch):
    inserted = _patch_common(monkeypatch, catalogued={"asset-1"})
    monkeypatch.setattr(
        catalog,
        "invoke",
        _fake_invoke([{"id": "asset-1", "filename": "a.jpg"}, {"id": "asset-2", "filename": "b.jpg"}]),
    )
    monkeypatch.setattr(
        catalog.vision, "describe", AsyncMock(return_value=[_item("new shirt")])
    )

    result = await catalog.sync("sub-noah")

    assert result["catalogued"] == 1
    assert [asset for asset, _ in inserted] == ["asset-2"]


async def test_force_recatalogues_everything(monkeypatch):
    inserted = _patch_common(monkeypatch, catalogued={"asset-1"})
    monkeypatch.setattr(
        catalog, "invoke", _fake_invoke([{"id": "asset-1", "filename": "a.jpg"}])
    )
    monkeypatch.setattr(
        catalog.vision, "describe", AsyncMock(return_value=[_item("shirt")])
    )

    result = await catalog.sync("sub-noah", force=True)

    assert result["catalogued"] == 1
    assert [asset for asset, _ in inserted] == ["asset-1"]


async def test_one_photo_can_yield_several_garments(monkeypatch):
    inserted = _patch_common(monkeypatch)
    monkeypatch.setattr(
        catalog, "invoke", _fake_invoke([{"id": "asset-1", "filename": "rail.jpg"}])
    )
    monkeypatch.setattr(
        catalog.vision,
        "describe",
        AsyncMock(return_value=[_item("shirt a"), _item("shirt b"), _item("shirt c")]),
    )

    result = await catalog.sync("sub-noah")

    assert result["catalogued"] == 3
    assert [row["name"] for row in inserted[0][1]] == ["shirt a", "shirt b", "shirt c"]


async def test_an_empty_successful_photo_is_not_redescribed_or_flagged_stale(monkeypatch):
    catalogued = set()
    monkeypatch.setattr(catalog, "album_for", lambda _sub: "album-1")
    monkeypatch.setattr(
        catalog, "invoke", _fake_invoke([{"id": "asset-empty", "filename": "empty.jpg"}])
    )
    monkeypatch.setattr(
        catalog.store, "catalogued_asset_ids", AsyncMock(side_effect=lambda _sub: set(catalogued))
    )

    async def _insert(_member_sub, asset_id, _items):
        catalogued.add(asset_id)

    monkeypatch.setattr(catalog.store, "insert_items", AsyncMock(side_effect=_insert))
    monkeypatch.setattr(catalog.store, "delete_assets", AsyncMock())
    describe = AsyncMock(return_value=[])
    monkeypatch.setattr(catalog.vision, "describe", describe)

    await catalog.sync("sub-noah")
    await catalog.sync("sub-noah")
    monkeypatch.setattr(catalog.store, "list_items", AsyncMock(return_value=[{"name": "shirt", "category": "top", "attrs": {}}]))

    rendered = await catalog.render_wardrobe("sub-noah")

    assert describe.await_count == 1
    assert "not been catalogued" not in rendered


async def test_assets_that_left_the_album_are_deleted(monkeypatch):
    _patch_common(monkeypatch, catalogued={"asset-1", "asset-gone"})
    monkeypatch.setattr(
        catalog, "invoke", _fake_invoke([{"id": "asset-1", "filename": "a.jpg"}])
    )
    monkeypatch.setattr(catalog.vision, "describe", AsyncMock(return_value=[]))

    result = await catalog.sync("sub-noah")

    assert result["removed"] == 1
    catalog.store.delete_assets.assert_awaited_once()
    assert catalog.store.delete_assets.await_args.args[1] == ["asset-gone"]


async def test_one_failing_photo_does_not_stop_the_others(monkeypatch):
    inserted = _patch_common(monkeypatch)
    monkeypatch.setattr(
        catalog,
        "invoke",
        _fake_invoke([{"id": "asset-1", "filename": "a.jpg"}, {"id": "asset-2", "filename": "b.jpg"}]),
    )

    calls = {"n": 0}

    async def _describe(image_base64, content_type):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the model hiccuped")
        return [_item("survivor")]

    monkeypatch.setattr(catalog.vision, "describe", _describe)

    result = await catalog.sync("sub-noah")

    assert result["failed"] == 1
    assert result["catalogued"] == 1
    assert [asset for asset, _ in inserted] == ["asset-2"]


async def test_a_limit_leaves_the_rest_for_next_time(monkeypatch):
    inserted = _patch_common(monkeypatch)
    monkeypatch.setattr(
        catalog,
        "invoke",
        _fake_invoke([{"id": f"asset-{n}", "filename": f"{n}.jpg"} for n in range(5)]),
    )
    monkeypatch.setattr(
        catalog.vision, "describe", AsyncMock(return_value=[_item("shirt")])
    )

    result = await catalog.sync("sub-noah", limit=2)

    assert result["catalogued"] == 2
    assert result["remaining"] == 3
    assert len(inserted) == 2


async def test_a_member_with_no_album_gets_a_clear_error(monkeypatch):
    monkeypatch.setattr(catalog, "album_for", lambda _sub: None)

    result = await catalog.sync("sub-noah")

    assert result["error"] == catalog.NO_ALBUM
    assert result["catalogued"] == 0


async def test_an_immich_failure_is_returned_not_raised(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        catalog, "invoke", AsyncMock(return_value="error: eve-tools unavailable")
    )

    result = await catalog.sync("sub-noah")

    assert result["error"].startswith("error:")
    assert result["catalogued"] == 0


async def test_a_truncated_album_listing_is_refused_not_reconciled(monkeypatch):
    """The regression the whole-branch review demanded: an asset outside the
    returned window must not be treated as departed. The 500-asset cap makes
    the listing partial, and a partial listing is never authoritative."""
    _patch_common(monkeypatch, catalogued={"asset-1", "asset-outside-the-window"})
    monkeypatch.setattr(
        catalog,
        "invoke",
        _fake_invoke([{"id": "asset-1", "filename": "a.jpg"}], truncated=True),
    )

    result = await catalog.sync("sub-noah")

    assert result["error"] == catalog.TRUNCATED
    assert result["catalogued"] == 0
    assert result["removed"] == 0
    catalog.store.delete_assets.assert_not_awaited()


async def test_a_truncated_refusal_records_zero_telemetry(monkeypatch):
    _patch_common(monkeypatch, catalogued={"asset-1"})
    spans = _record_telemetry(monkeypatch)
    monkeypatch.setattr(
        catalog,
        "invoke",
        _fake_invoke([{"id": "asset-1", "filename": "a.jpg"}], truncated=True),
    )

    await catalog.sync("sub-noah")

    assert ("eve.wardrobe.items_catalogued", 0) in spans
    assert ("eve.wardrobe.vision_failures", 0) in spans


async def test_sync_records_the_spec_telemetry(monkeypatch):
    """The spec's two sync questions: is the vision pass working, and on what
    fraction. One failing photo beside one good one must show both numbers."""
    inserted = _patch_common(monkeypatch)
    spans = _record_telemetry(monkeypatch)
    monkeypatch.setattr(
        catalog,
        "invoke",
        _fake_invoke([{"id": "asset-1", "filename": "a.jpg"}, {"id": "asset-2", "filename": "b.jpg"}]),
    )

    calls = {"n": 0}

    async def _describe(image_base64, content_type):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the model hiccuped")
        return [_item("survivor")]

    monkeypatch.setattr(catalog.vision, "describe", _describe)

    result = await catalog.sync("sub-noah")

    assert result["catalogued"] == 1
    assert result["failed"] == 1
    assert ("span.name", "eve.wardrobe.sync") in spans
    assert ("eve.wardrobe.items_catalogued", 1) in spans
    assert ("eve.wardrobe.vision_failures", 1) in spans


async def test_render_groups_by_category_and_names_every_item(monkeypatch):
    monkeypatch.setattr(catalog, "album_for", lambda _sub: "album-1")
    monkeypatch.setattr(
        catalog.store,
        "list_items",
        AsyncMock(
            return_value=[
                {"name": "navy wool blazer", "category": "outerwear", "attrs": {"warmth": 4, "formality": 4, "fabric": "wool"}},
                {"name": "white oxford shirt", "category": "top", "attrs": {"warmth": 2, "formality": 3, "fabric": "cotton"}},
            ]
        ),
    )
    monkeypatch.setattr(catalog, "invoke", _fake_invoke([{"id": "a", "filename": "x"}, {"id": "b", "filename": "y"}]))
    monkeypatch.setattr(
        catalog.store, "catalogued_asset_ids", AsyncMock(return_value={"a", "b"})
    )

    rendered = await catalog.render_wardrobe("sub-noah")

    assert "outerwear" in rendered
    assert "navy wool blazer" in rendered
    assert "white oxford shirt" in rendered
    assert "warmth 4" in rendered


async def test_render_reports_an_empty_wardrobe(monkeypatch):
    monkeypatch.setattr(catalog, "album_for", lambda _sub: "album-1")
    monkeypatch.setattr(catalog.store, "list_items", AsyncMock(return_value=[]))
    monkeypatch.setattr(catalog, "invoke", _fake_invoke([]))

    assert await catalog.render_wardrobe("sub-noah") == catalog.EMPTY


async def test_render_flags_a_stale_catalogue(monkeypatch):
    monkeypatch.setattr(catalog, "album_for", lambda _sub: "album-1")
    monkeypatch.setattr(catalog.store, "list_items", AsyncMock(return_value=[{"name": "shirt", "category": "top", "attrs": {}}]))
    monkeypatch.setattr(catalog, "invoke", _fake_invoke([{"id": "a", "filename": "x"}, {"id": "b", "filename": "y"}, {"id": "c", "filename": "z"}]))
    monkeypatch.setattr(catalog.store, "catalogued_asset_ids", AsyncMock(return_value={"a"}))

    rendered = await catalog.render_wardrobe("sub-noah")

    assert "2 photo" in rendered
    assert "not been catalogued" in rendered


async def test_render_survives_immich_being_down_and_says_so(monkeypatch):
    """A failed staleness CHECK is not an all-clear: the render still works,
    but the member is told the list may be out of date."""
    monkeypatch.setattr(catalog, "album_for", lambda _sub: "album-1")
    monkeypatch.setattr(catalog.store, "list_items", AsyncMock(return_value=[{"name": "shirt", "category": "top", "attrs": {}}]))
    monkeypatch.setattr(catalog, "invoke", AsyncMock(return_value="error: down"))

    rendered = await catalog.render_wardrobe("sub-noah")

    assert "shirt" in rendered
    assert "could not be reached" in rendered


async def test_render_warns_when_the_album_is_too_big_to_check(monkeypatch):
    monkeypatch.setattr(catalog, "album_for", lambda _sub: "album-1")
    monkeypatch.setattr(catalog.store, "list_items", AsyncMock(return_value=[{"name": "shirt", "category": "top", "attrs": {}}]))
    monkeypatch.setattr(
        catalog,
        "invoke",
        _fake_invoke([{"id": "a", "filename": "x"}], truncated=True),
    )

    rendered = await catalog.render_wardrobe("sub-noah")

    assert "incomplete or out of date" in rendered


async def test_render_records_the_stale_count(monkeypatch):
    """The spec's drift question, answered with a number on every read."""
    monkeypatch.setattr(catalog, "album_for", lambda _sub: "album-1")
    spans = _record_telemetry(monkeypatch)
    monkeypatch.setattr(catalog.store, "list_items", AsyncMock(return_value=[{"name": "shirt", "category": "top", "attrs": {}}]))
    monkeypatch.setattr(
        catalog,
        "invoke",
        _fake_invoke([{"id": "a", "filename": "x"}, {"id": "b", "filename": "y"}, {"id": "c", "filename": "z"}]),
    )
    monkeypatch.setattr(catalog.store, "catalogued_asset_ids", AsyncMock(return_value={"a"}))

    await catalog.render_wardrobe("sub-noah")

    assert ("span.name", "eve.wardrobe.read") in spans
    assert ("eve.wardrobe.stale_count", 2) in spans


async def test_render_records_zero_when_the_stale_check_cannot_run(monkeypatch):
    monkeypatch.setattr(catalog, "album_for", lambda _sub: "album-1")
    spans = _record_telemetry(monkeypatch)
    monkeypatch.setattr(catalog.store, "list_items", AsyncMock(return_value=[{"name": "shirt", "category": "top", "attrs": {}}]))
    monkeypatch.setattr(catalog, "invoke", AsyncMock(return_value="error: down"))

    await catalog.render_wardrobe("sub-noah")

    assert ("eve.wardrobe.stale_count", 0) in spans
