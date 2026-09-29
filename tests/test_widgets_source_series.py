from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


def _recipe(**overrides) -> dict:
    recipe = {
        "sources": [{"type": "records", "collection": "alpha.thing"}],
        "metric": {"op": "count"},
    }
    recipe.update(overrides)
    return recipe


def test_a_minimal_records_recipe_is_valid():
    from eve.widgets.sources.series import validate_v1

    assert validate_v1(_recipe()) is None


def test_a_health_source_is_valid():
    from eve.widgets.sources.series import validate_v1

    assert validate_v1(
        _recipe(sources=[{"type": "health", "metric": "activity"}])
    ) is None


def test_an_unknown_source_type_is_rejected():
    from eve.widgets.sources.series import validate_v1

    assert validate_v1(
        _recipe(sources=[{"type": "http", "url": "https://example.com"}])
    ) == "source-type"


def test_a_recipe_cannot_name_a_member():
    """Identity comes from the authenticated principal, never the recipe."""
    from eve.widgets.sources.series import validate_v1

    assert validate_v1(
        _recipe(sources=[{
            "type": "records",
            "collection": "alpha.thing",
            "member_sub": "sub-kendra",
        }])
    ) == "source-schema"


def test_a_records_source_needs_a_collection():
    from eve.widgets.sources.series import validate_v1

    assert validate_v1(
        _recipe(sources=[{"type": "records"}])
    ) == "source-schema"


def test_a_recipe_with_no_sources_is_rejected():
    from eve.widgets.sources.series import validate_v1

    assert validate_v1(_recipe(sources=[])) == "sources"


def test_too_many_sources_are_rejected():
    from eve.widgets.sources import series

    sources = [
        {"type": "records", "collection": f"c{i}"} for i in range(series.MAX_SOURCES + 1)
    ]
    assert series.validate_v1(_recipe(sources=sources)) == "sources"


def test_an_unknown_metric_op_is_rejected():
    from eve.widgets.sources.series import validate_v1

    assert validate_v1(_recipe(metric={"op": "exfiltrate"})) == "metric"


def test_a_sum_metric_needs_a_field():
    from eve.widgets.sources.series import validate_v1

    assert validate_v1(_recipe(metric={"op": "sum"})) == "metric"
    assert validate_v1(_recipe(metric={"op": "sum", "field": "weight"})) is None


def test_a_non_dict_recipe_is_rejected():
    from eve.widgets.sources.series import validate_v1

    assert validate_v1("nonsense") == "recipe"
    assert validate_v1(None) == "recipe"


def test_an_unknown_top_level_key_is_rejected():
    """Only `sources` and `metric` are legal at the top level."""
    from eve.widgets.sources.series import validate_v1

    assert validate_v1(_recipe(exec="rm -rf")) == "recipe"


def test_an_oversized_recipe_is_rejected(monkeypatch):
    """The total-size cap is a backstop: the per-field bounds keep every
    legal recipe well under `MAX_RECIPE_BYTES`, so shrink the cap to
    exercise the branch."""
    from eve.widgets.sources import series

    monkeypatch.setattr(series, "MAX_RECIPE_BYTES", 64)
    assert series.validate_v1(_recipe()) == "recipe"


def test_a_maximum_legal_recipe_stays_under_the_size_cap():
    """No recipe the other checks accept may trip the overall size cap."""
    from eve.widgets.sources import series

    sources = [
        {"type": "records", "collection": "c" * series.MAX_NAME}
        for _ in range(series.MAX_SOURCES)
    ]
    assert series.validate_v1(
        _recipe(
            sources=sources,
            metric={"op": "sum", "field": "f" * series.MAX_NAME},
        )
    ) is None


async def test_series_reads_health_from_the_keyed_list(monkeypatch):
    """Regression: v1 expected a bare list and raised on every call."""
    from eve.widgets.sources import base, series

    async def fake_invoke(tool, arguments):
        assert tool == "health.get_recovery"
        return json.dumps({"recovery": [{"date": "2026-09-27", "score_0_100": 71}]})

    monkeypatch.setattr(base, "invoke", fake_invoke)
    params = series.SeriesParams(
        sources=[{"type": "health", "metric": "recovery"}],
        metric={"op": "max", "field": "score_0_100"},
    )
    out = await series._read(base.ReadContext("sub-noah", {"days": 7}), params)

    assert out["points"] == [{"label": "2026-09-27", "value": 71.0, "source": "health"}]
    assert out["note"] == ""


async def test_series_notes_an_empty_result(monkeypatch):
    from eve.widgets.sources import base, series

    async def no_rows(member_sub, collection, since=None, until=None, limit=500):
        return []

    monkeypatch.setattr(series.record_store, "query", no_rows)
    params = series.SeriesParams(
        sources=[{"type": "records", "collection": "alpha"}], metric={"op": "count"}
    )
    out = await series._read(base.ReadContext("sub-noah", {}), params)
    assert out["points"] == [] and out["note"].startswith("Nothing recorded")


def test_validate_v1_keeps_its_codes():
    from eve.widgets.sources.series import validate_v1

    assert validate_v1({"sources": [{"type": "sql"}], "metric": {"op": "count"}}) == "source-type"
    assert validate_v1({"sources": [], "metric": {"op": "count"}}) == "sources"
