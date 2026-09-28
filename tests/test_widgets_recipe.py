"""A recipe is executed later with no model in the loop, so everything it is
allowed to say has to be decided here.

The v1 source/metric validation-code tests that used to live here (asserting
`recipe.validate(...) == "source-type"` etc.) moved to
`tests/test_widgets_source_series.py`, calling `series.validate_v1` instead
(Task A1, ENG-269 Step 7). What remains here is filters, permissions and
`KINDS`, which Task A6 will deal with along with the rest of this file.
"""
from __future__ import annotations


def _recipe(**overrides) -> dict:
    recipe = {
        "sources": [{"type": "records", "collection": "alpha.thing"}],
        "metric": {"op": "count"},
    }
    recipe.update(overrides)
    return recipe


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