"""What a saved widget is allowed to say.

A recipe is authored once, by a model, and then executed on every refresh
with no model in the loop and no human reading it. That makes this module the
security boundary for the whole feature: anything it accepts runs
indefinitely.

So the vocabulary is closed and small. A source names a KIND of read
(`records`, `health`), never a URL, a tool name, a SQL fragment, or a
member. Identity is supplied by the authenticated route at execution time,
which is why a recipe that tries to name a member is rejected outright rather
than having the field ignored.

Pure module: no I/O, no database, no LangGraph.
"""

from __future__ import annotations

import json

KINDS = frozenset({"chart"})

# A source type maps to one audited reader in `eve.widgets.resolve`. Adding an
# external system means adding a reader here and in that module - deliberately
# a code change with a review, because credentials and normalisation cannot be
# authored by a model. Adding a new WIDGET costs nothing.
SOURCE_TYPES = frozenset({"records", "health"})

# Permission required per source type, per the spec: reading the member's own
# records needs nothing beyond being that member.
SOURCE_PERMISSIONS: dict[str, str | None] = {
    "records": None,
    "health": "health",
}

HEALTH_METRICS = frozenset({"recovery", "sleep", "activity"})
METRIC_OPS = frozenset({"count", "sum", "avg", "max"})
FILTER_KEYS = frozenset({"days", "sources", "field", "groupBy"})

MAX_SOURCES = 4
MAX_DAYS = 3650
MAX_NAME = 128

# The protocol's definition ceiling is 48KiB, but a widget recipe is authored
# once and executed forever, so the total body is bounded far below that:
# 4KiB of JSON is ample for a handful of sources and one metric. This is a
# backstop over the per-field bounds, not the primary defense.
MAX_RECIPE_BYTES = 4_096


def validate(candidate: object) -> str | None:
    """`None` when `candidate` is a legal recipe, else a diagnostic code."""
    if not isinstance(candidate, dict):
        return "recipe"

    if set(candidate) - {"sources", "metric"}:
        # Catches `exec`, `url`, `token` and every other smuggled key at the
        # top level, the same way the source and metric interiors already do.
        return "recipe"

    sources = candidate.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        return "sources"
    for source in sources:
        error = _validate_source(source)
        if error:
            return error

    error = _validate_metric(candidate.get("metric"))
    if error:
        return error

    if len(json.dumps(candidate).encode()) > MAX_RECIPE_BYTES:
        return "recipe"

    return None


def _validate_source(source: object) -> str | None:
    if not isinstance(source, dict):
        return "source-schema"
    kind = source.get("type")
    if kind not in SOURCE_TYPES:
        return "source-type"

    allowed = {"type", "collection"} if kind == "records" else {"type", "metric"}
    if set(source) - allowed:
        # Catches `member_sub`, `url`, `token` and every other smuggled key.
        return "source-schema"

    if kind == "records":
        collection = source.get("collection")
        if not isinstance(collection, str) or not 0 < len(collection) <= MAX_NAME:
            return "source-schema"
    else:
        if source.get("metric") not in HEALTH_METRICS:
            return "source-schema"
    return None


def _validate_metric(metric: object) -> str | None:
    if not isinstance(metric, dict):
        return "metric"
    op = metric.get("op")
    if op not in METRIC_OPS:
        return "metric"
    if set(metric) - {"op", "field"}:
        return "metric"
    if op == "count":
        return None
    field = metric.get("field")
    if not isinstance(field, str) or not 0 < len(field) <= MAX_NAME:
        return "metric"
    return None


def validate_filters(candidate: object) -> str | None:
    """Filters are member-supplied on every refresh, so they are bounded
    independently of the recipe that was authored once."""
    if not isinstance(candidate, dict):
        return "filters"
    if set(candidate) - FILTER_KEYS:
        return "filters"

    if "days" in candidate:
        days = candidate["days"]
        if isinstance(days, bool) or not isinstance(days, int):
            return "filters"
        if not 1 <= days <= MAX_DAYS:
            return "filters"

    if "sources" in candidate:
        chosen = candidate["sources"]
        if not isinstance(chosen, list):
            return "filters"
        if any(entry not in SOURCE_TYPES for entry in chosen):
            return "filters"

    for key in ("field", "groupBy"):
        if key in candidate:
            value = candidate[key]
            if not isinstance(value, str) or not 0 < len(value) <= MAX_NAME:
                return "filters"
    return None


def required_permissions(recipe: dict) -> list[str]:
    """Permissions this recipe needs, derived from the sources it reads.

    Per source rather than per widget kind: two charts can need different
    permissions, and the kind says nothing about what is being read.
    """
    needed = {
        SOURCE_PERMISSIONS.get(source.get("type"))
        for source in recipe.get("sources", [])
        if isinstance(source, dict)
    }
    return sorted(permission for permission in needed if permission)