# src/eve/widgets/resolve.py
"""Recipe to rendered snapshot, with no model in the loop.

Reads every source concurrently through the source registry, renders the
template against the results, and publishes what the client needs to behave
well without knowing anything about Eve: when to refresh, and which actions
on which targets need a confirmation.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from eve.ui import protocol
from eve.widgets import recipe as recipe_rules
from eve.widgets import template as template_rules
from eve.widgets.actions import REGISTRY as ACTIONS
from eve.widgets.sources.base import ReadContext
from eve.widgets.sources.series import DEFAULT_DAYS

logger = logging.getLogger(__name__)

_BROKEN = [{"id": "broken", "type": "text", "properties": {"text": "This widget couldn't be displayed."}, "children": []}]


async def snapshot(resource: dict, member_sub: str) -> dict:
    """Never raises: a broken source is `partial`, a broken template is the
    `_BROKEN` view, and both still carry a valid revision the client can act on."""
    spec = recipe_rules.upgrade(resource.get("recipe") or {})
    filters = resource.get("filters") or {}
    parsed = recipe_rules.parse_sources(spec)
    if isinstance(parsed, str):
        logger.warning("widget %s has an invalid recipe: %s", resource.get("id"), parsed)
        parsed = {}

    ctx = ReadContext(member_sub=member_sub, filters=filters)

    async def read(alias, source, params):
        try:
            return alias, await source.read(ctx, params), None
        except Exception:
            # Structural diagnostics only: upstream messages can carry a
            # token, a DSN or member data, and this goes over HTTP.
            logger.warning("widget source %s (%s) failed", alias, source.name, exc_info=True)
            return alias, {}, {"source": alias, "reason": "unavailable"}

    results = await asyncio.gather(*(read(a, s, p) for a, (s, p) in parsed.items()))
    data: dict = {alias: value for alias, value, _ in results}
    errors = [error for _, _, error in results if error]
    data[template_rules.WIDGET_ALIAS] = {
        "title": resource.get("title", ""),
        "days": str(filters.get("days", DEFAULT_DAYS)),
        "empty_points": [],
    }

    components, _problems = template_rules.render(spec.get("template") or [], data)
    candidate = {"protocol": protocol.PROTOCOL, "op": "create", "surface": {
        "surfaceId": f"widget:{resource['id']}", "catalogId": "column", "catalogVersion": protocol.CATALOG_VERSION,
        "components": components, "data": data, "localState": {}}}
    error = protocol.validate_operation(candidate, widget=True)
    if error:
        logger.warning("widget %s rendered an invalid surface: %s", resource.get("id"), error)
        components, data = _BROKEN, {template_rules.WIDGET_ALIAS: data[template_rules.WIDGET_ALIAS]}

    return {
        "resourceId": resource["id"],
        "kind": resource["kind"],
        "revision": resource["revision"],
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "filters": filters,
        "refreshAfterSeconds": recipe_rules.ttl_seconds(spec),
        "actionRisk": _risk_overrides(spec),
        "view": {"components": components, "data": data},
        "sources": {"partial": bool(errors), "errors": errors},
    }


def _risk_overrides(spec: dict) -> dict[str, str]:
    """`<action>:<target>` -> risk, only where it differs from the action's
    advertised default, so the common case costs nothing on the wire."""
    overrides: dict[str, str] = {}
    for target in sorted(recipe_rules.declared_targets(spec)):
        for action in ACTIONS.values():
            if not action.targeted:
                continue
            risk = action.risk_for(target)
            if risk is not None and risk != action.default_risk:
                overrides[f"{action.name}:{target}"] = risk
    return overrides
