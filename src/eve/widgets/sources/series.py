"""A day-bucketed series from recorded collections and/or health metrics.

`validate_v1` is the v1 chart recipe's grammar, moved here verbatim from
`eve.widgets.recipe.validate` (codes unchanged) so `SeriesParams` can reuse it
as the params of one source instead of a whole widget kind. `recipe.py` keeps
its own copy until Task A6 replaces it wholesale.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, model_validator

from eve.records import store as record_store
from eve.widgets.sources.base import ReadContext, SourceError, SourceType, call_tool, register

HEALTH_METRICS = frozenset({"recovery", "sleep", "activity"})
METRIC_OPS = frozenset({"count", "sum", "avg", "max"})
SOURCE_TYPES = frozenset({"records", "health"})

MAX_SOURCES = 4
MAX_NAME = 128

# The protocol's definition ceiling is 48KiB, but a widget recipe is authored
# once and executed forever, so the total body is bounded far below that:
# 4KiB of JSON is ample for a handful of sources and one metric. This is a
# backstop over the per-field bounds, not the primary defense.
MAX_RECIPE_BYTES = 4_096

DEFAULT_DAYS = 30
MAX_POINTS = 180


def validate_v1(candidate: object) -> str | None:
    """`None` when `candidate` is a legal v1 recipe, else a diagnostic code."""
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


def _points_from_records(rows: list[dict], metric: dict) -> list[dict]:
    """Bucket by local day, then apply the metric.

    A value that cannot be read as a number is SKIPPED, never coerced to
    zero: a missing measurement and a measured zero are different facts, and
    a chart that conflates them is quietly wrong.
    """
    buckets: dict[str, list[float]] = {}
    for row in rows:
        occurred = row.get("occurred_at")
        label = occurred.date().isoformat() if hasattr(occurred, "date") else str(occurred)
        if metric["op"] == "count":
            buckets.setdefault(label, []).append(1.0)
            continue
        raw = (row.get("payload") or {}).get(metric.get("field"))
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        buckets.setdefault(label, []).append(float(raw))

    return [
        {"label": label, "value": _apply(metric["op"], values), "source": "records"}
        for label, values in buckets.items()
        if values
    ]


def _points_from_health(rows: list[dict], metric: dict) -> list[dict]:
    field = metric.get("field")
    points = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("date", ""))
        raw = row.get(field) if field else 1
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        points.append({"label": label, "value": float(raw), "source": "health"})
    return points


def _apply(op: str, values: list[float]) -> float:
    if op == "count":
        return float(len(values))
    if op == "sum":
        return float(sum(values))
    if op == "avg":
        return float(sum(values) / len(values))
    return float(max(values))


class SeriesParams(BaseModel):
    """The v1 chart recipe, unchanged, as the params of one source."""

    model_config = ConfigDict(extra="forbid")
    sources: list[dict]
    metric: dict

    @model_validator(mode="after")
    def _v1_rules(self) -> "SeriesParams":
        error = validate_v1({"sources": self.sources, "metric": self.metric})
        if error:
            raise ValueError(f"series recipe rejected: {error}")
        return self


async def _read_health_rows(member_sub: str, metric: str, days: int) -> list[dict]:
    # `health.get_<metric>` answers {"<metric>": [...], "errors"?: [...]}, NOT
    # a list. The v1 reader expected a list and so raised on every call: every
    # health widget rendered "Some data unavailable" (found while planning
    # ENG-269). Unwrap the keyed list.
    body = await call_tool(f"health.get_{metric}", {"member_sub": member_sub, "days": days})
    rows = body.get(metric)
    if not isinstance(rows, list):
        raise SourceError(f"health.get_{metric} had no {metric} list")
    return rows


async def _read(ctx: ReadContext, params: SeriesParams) -> dict:
    days = ctx.filters.get("days", DEFAULT_DAYS)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    points: list[dict] = []
    for source in params.sources:
        if source["type"] == "records":
            rows = await record_store.query(ctx.member_sub, source["collection"], since=since)
            points.extend(_points_from_records(rows, params.metric))
        else:
            rows = await _read_health_rows(ctx.member_sub, source["metric"], days)
            points.extend(_points_from_health(rows, params.metric))
    points.sort(key=lambda point: point["label"])
    points = points[-MAX_POINTS:]
    return {
        "points": points,
        # A chart with no bars looks broken; the most likely cause is a
        # collection name that never matched, which the member can act on.
        "note": "" if points else "Nothing recorded yet for this widget.",
    }


def _permissions(params: SeriesParams) -> frozenset[str]:
    return frozenset({"health"}) if any(s["type"] == "health" for s in params.sources) else frozenset()


register(SourceType(
    name="series",
    params=SeriesParams,
    fields=frozenset({"points", "note"}),
    read=_read,
    ttl_seconds=900,
    permissions=_permissions,
    description="A day-bucketed series from recorded collections and/or health metrics, for a chart. "
    "Params: sources (list of {type: records, collection} | {type: health, metric: recovery|sleep|activity}), "
    "metric ({op: count} | {op: sum|avg|max, field}). Honours the widget's days filter.",
))
