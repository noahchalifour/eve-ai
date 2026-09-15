"""A recipe is executed later with no model in the loop, so everything it is
allowed to say has to be decided here."""
from __future__ import annotations


def _recipe(**overrides) -> dict:
    recipe = {
        "sources": [{"type": "records", "collection": "alpha.thing"}],
        "metric": {"op": "count"},
    }
    recipe.update(overrides)
    return recipe


def test_a_minimal_records_recipe_is_valid():
    from eve.widgets import recipe

    assert recipe.validate(_recipe()) is None


def test_a_health_source_is_valid():
    from eve.widgets import recipe

    assert recipe.validate(
        _recipe(sources=[{"type": "health", "metric": "activity"}])
    ) is None


def test_an_unknown_source_type_is_rejected():
    from eve.widgets import recipe

    assert recipe.validate(
        _recipe(sources=[{"type": "http", "url": "https://example.com"}])
    ) == "source-type"


def test_a_recipe_cannot_name_a_member():
    """Identity comes from the authenticated principal, never the recipe."""
    from eve.widgets import recipe

    assert recipe.validate(
        _recipe(sources=[{
            "type": "records",
            "collection": "alpha.thing",
            "member_sub": "sub-kendra",
        }])
    ) == "source-schema"


def test_a_records_source_needs_a_collection():
    from eve.widgets import recipe

    assert recipe.validate(
        _recipe(sources=[{"type": "records"}])
    ) == "source-schema"


def test_a_recipe_with_no_sources_is_rejected():
    from eve.widgets import recipe

    assert recipe.validate(_recipe(sources=[])) == "sources"


def test_too_many_sources_are_rejected():
    from eve.widgets import recipe

    sources = [
        {"type": "records", "collection": f"c{i}"} for i in range(recipe.MAX_SOURCES + 1)
    ]
    assert recipe.validate(_recipe(sources=sources)) == "sources"


def test_an_unknown_metric_op_is_rejected():
    from eve.widgets import recipe

    assert recipe.validate(_recipe(metric={"op": "exfiltrate"})) == "metric"


def test_a_sum_metric_needs_a_field():
    from eve.widgets import recipe

    assert recipe.validate(_recipe(metric={"op": "sum"})) == "metric"
    assert recipe.validate(_recipe(metric={"op": "sum", "field": "weight"})) is None


def test_a_non_dict_recipe_is_rejected():
    from eve.widgets import recipe

    assert recipe.validate("nonsense") == "recipe"
    assert recipe.validate(None) == "recipe"


def test_an_unknown_top_level_key_is_rejected():
    """Only `sources` and `metric` are legal at the top level."""
    from eve.widgets import recipe

    assert recipe.validate(_recipe(exec="rm -rf")) == "recipe"


def test_an_oversized_recipe_is_rejected(monkeypatch):
    """The total-size cap is a backstop: the per-field bounds keep every
    legal recipe well under `MAX_RECIPE_BYTES`, so shrink the cap to
    exercise the branch."""
    from eve.widgets import recipe

    monkeypatch.setattr(recipe, "MAX_RECIPE_BYTES", 64)
    assert recipe.validate(_recipe()) == "recipe"


def test_a_maximum_legal_recipe_stays_under_the_size_cap():
    """No recipe the other checks accept may trip the overall size cap."""
    from eve.widgets import recipe

    sources = [
        {"type": "records", "collection": "c" * recipe.MAX_NAME}
        for _ in range(recipe.MAX_SOURCES)
    ]
    assert recipe.validate(
        _recipe(
            sources=sources,
            metric={"op": "sum", "field": "f" * recipe.MAX_NAME},
        )
    ) is None


def test_filters_accept_a_bounded_window():
    from eve.widgets import recipe

    assert recipe.validate_filters({"days": 30}) is None
    assert recipe.validate_filters({"days": 0}) == "filters"
    assert recipe.validate_filters({"days": recipe.MAX_DAYS + 1}) == "filters"
    assert recipe.validate_filters({"days": "thirty"}) == "filters"


def test_filters_reject_unknown_keys():
    from eve.widgets import recipe

    assert recipe.validate_filters({"exec": "rm -rf"}) == "filters"


def test_required_permissions_are_per_source():
    """A recipe reading only the member's own records needs nothing extra."""
    from eve.widgets import recipe

    assert recipe.required_permissions(_recipe()) == []
    assert recipe.required_permissions(
        _recipe(sources=[{"type": "health", "metric": "activity"}])
    ) == ["health"]


def test_no_kind_is_domain_specific():
    from eve.widgets import recipe

    assert recipe.KINDS == frozenset({"chart"})