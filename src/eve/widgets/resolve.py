"""Recipe plus filters to a rendered snapshot, with no model in the loop.

This is the module that makes "refresh without spending a model call" true.
It reads only through the two injected readers, which is what lets every
partial-failure branch be a unit test rather than a live-service test.

The emitted `components` tree uses only `assistant-ui/1.0` catalog types, so
the client validates and renders it with the same code path a chat surface
takes. `data.points` is the chart's bound series.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from eve.records import store as record_store
from eve.tools_client import invoke
from eve.widgets import recipe as recipe_rules

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 30
MAX_POINTS = 180


async def _default_read_records(
    member_sub: str, collection: str, since=None, until=None, limit=500
):
    return await record_store.query(
        member_sub, collection, since=since, until=until, limit=limit
    )


async def _default_read_health(member_sub: str, metric: str, days: int):
    result = await invoke(
        f"health.get_{metric}", {"member_sub": member_sub, "days": days}
    )
    return result if isinstance(result, list) else []


async def snapshot(
    resource: dict,
    member_sub: str,
    *,
    read_records=None,
    read_health=None,
) -> dict:
    """The full resource-level snapshot the client renders.

    Never raises. A source that fails contributes nothing and is reported in
    `sources.errors`, because a widget showing three of four series is more
    useful than one showing an error, and the client labels the gap.
    """
    read_records = read_records or _default_read_records
    read_health = read_health or _default_read_health

    spec = resource.get("recipe") or {}
    filters = resource.get("filters") or {}
    days = filters.get("days", DEFAULT_DAYS)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    metric = spec.get("metric") or {"op": "count"}

    points: list[dict] = []
    errors: list[dict] = []

    for source in spec.get("sources", []):
        kind = source.get("type")
        try:
            if kind == "records":
                rows = await read_records(
                    member_sub, source["collection"], since=since
                )
                points.extend(_points_from_records(rows, metric))
            elif kind == "health":
                rows = await read_health(member_sub, source["metric"], days)
                points.extend(_points_from_health(rows, metric))
        except Exception:
            # Structural diagnostics only. The upstream message can carry a
            # DSN, a token, or a member's data, and this snapshot is returned
            # over HTTP to a client.
            logger.warning("widget source %s failed", kind, exc_info=True)
            errors.append({"source": kind, "reason": "unavailable"})

    points.sort(key=lambda point: point["label"])
    points = points[-MAX_POINTS:]

    return {
        "resourceId": resource["id"],
        "kind": resource["kind"],
        "revision": resource["revision"],
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "filters": filters,
        "view": {
            "components": _components(resource, points, errors),
            "data": {"points": points, "title": resource.get("title", "")},
        },
        "sources": {"partial": bool(errors), "errors": errors},
    }


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


def _components(resource: dict, points: list[dict], errors: list[dict]) -> list[dict]:
    """The rendered tree. Catalog types only.

    An empty series renders a sentence rather than a blank chart: a chart with
    no bars looks broken, and the most likely cause is a collection name that
    never matched anything, which the member can act on.
    """
    children: list[dict] = [_range_control(resource)]
    if points:
        children.append({"id": "chart", "type": "chart", "properties": {
            "points": "$data.points",
        }})
    else:
        children.append({"id": "empty", "type": "text", "properties": {
            "text": "Nothing recorded yet for this widget.",
        }})
    if errors:
        children.append({"id": "partial", "type": "badge", "properties": {
            "label": "Some data unavailable",
        }})

    return [{
        "id": "root",
        "type": "card",
        "properties": {"title": resource.get("title", "")},
        "children": children,
    }]


def _range_control(resource: dict) -> dict:
    """The inline filter, rendered as part of the snapshot.

    It is a `segmentedSelection` whose `actionId` is `widget.setRange`, an id
    the CHAT protocol does not allow - `assistant-ui/1.0` allowlists only
    `surface.submit`. That is deliberate and it is why the widget host
    intercepts this id before the generic renderer ever dispatches it: a
    widget filter is a resource action, not a chat turn, and the two must not
    share a channel.

    The selected option comes from the resource's own filters, so the control
    always shows the state the server actually holds rather than a local guess
    that can drift from it.
    """
    filters = resource.get("filters") or {}
    selected = str(filters.get("days", DEFAULT_DAYS))
    return {
        "id": "range",
        "type": "segmentedSelection",
        "properties": {
            "options": ["7", "30", "90"],
            "selected": selected,
            "actionId": "widget.setRange",
        },
    }