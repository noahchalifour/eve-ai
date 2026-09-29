# src/eve/widgets/recipe.py
"""What a saved widget is allowed to say.

A recipe is authored once, by a model, and executed on every refresh with no
model in the loop and no human reading it, so this module is still the
security boundary. v2 recipes are `{version, sources, template}`:

- `sources` maps an alias to a registered source type and its declared params
  (`eve.widgets.sources`). A source never names a URL, token, tool or member.
- `template` is a component tree (`eve.widgets.template`) whose actions may
  only target what those sources declared.

v1 recipes (`{sources: [...], metric}`) still exist in the database; they are
upgraded on read to the `chart` preset, never rewritten in place.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable

from pydantic import BaseModel

from eve.widgets import sources as source_registry
from eve.widgets import template as template_rules
from eve.widgets.sources.base import SourceType
from eve.widgets.sources.series import MAX_DAYS, SOURCE_TYPES as V1_SOURCE_TYPES  # noqa: F401

RECIPE_VERSION = 2
MAX_SOURCES = 4
MAX_NAME = 128
MAX_RECIPE_BYTES = 16_384
FILTER_KEYS = frozenset({"days", "sources", "field", "groupBy"})
_ALIAS = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def upgrade(recipe: dict) -> dict:
    if isinstance(recipe, dict) and recipe.get("version") == RECIPE_VERSION:
        return recipe
    from eve.widgets import presets  # presets builds recipes; import late to keep the graph acyclic
    built = presets.build("chart", recipe if isinstance(recipe, dict) else {})
    if isinstance(built, str):
        # A stored v1 recipe that no longer validates renders the error view
        # rather than raising in a GET.
        return {"version": RECIPE_VERSION, "sources": {}, "template": []}
    return built


def parse_sources(recipe: dict) -> dict[str, tuple[SourceType, BaseModel]] | str:
    raw = recipe.get("sources")
    if not isinstance(raw, dict) or not 1 <= len(raw) <= MAX_SOURCES:
        return f"sources must be an object with 1-{MAX_SOURCES} entries"
    parsed: dict[str, tuple[SourceType, BaseModel]] = {}
    for alias, spec in raw.items():
        if not _ALIAS.match(alias) or alias == template_rules.WIDGET_ALIAS:
            return f"source alias {alias!r} must be lowercase letters/digits/underscores and not 'widget'"
        result = source_registry.parse(spec)
        if isinstance(result, str):
            return f"{alias}: {result}"
        parsed[alias] = result
    return parsed


def validate(candidate: object, *, action_accepts: Callable[[str, str], bool]) -> str | None:
    if not isinstance(candidate, dict):
        return "recipe must be an object"
    if set(candidate) != {"version", "sources", "template"} or candidate["version"] != RECIPE_VERSION:
        return "recipe must be exactly {version: 2, sources, template}"
    if len(json.dumps(candidate).encode()) > MAX_RECIPE_BYTES:
        return f"recipe is larger than {MAX_RECIPE_BYTES} bytes"
    parsed = parse_sources(candidate)
    if isinstance(parsed, str):
        return parsed
    return template_rules.validate(candidate["template"], parsed, action_accepts=action_accepts)


def validate_filters(candidate: object) -> str | None:
    """Filters are member-supplied on every refresh, so they are bounded
    independently of the recipe that was authored once. (Body unchanged from v1.)"""
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
        if any(entry not in V1_SOURCE_TYPES for entry in chosen):
            return "filters"
    for key in ("field", "groupBy"):
        if key in candidate:
            value = candidate[key]
            if not isinstance(value, str) or not 0 < len(value) <= MAX_NAME:
                return "filters"
    return None


def _parsed_or_empty(recipe: dict) -> dict[str, tuple[SourceType, BaseModel]]:
    parsed = parse_sources(upgrade(recipe))
    return {} if isinstance(parsed, str) else parsed


def required_permissions(recipe: dict) -> list[str]:
    needed: set[str] = set()
    for source, params in _parsed_or_empty(recipe).values():
        needed |= source.permissions(params)
    return sorted(needed)


def declared_targets(recipe: dict) -> frozenset[str]:
    targets: set[str] = set()
    for source, params in _parsed_or_empty(recipe).values():
        targets |= source.targets(params)
    return frozenset(targets)


def ttl_seconds(recipe: dict) -> int:
    parsed = _parsed_or_empty(recipe)
    return min((source.ttl_seconds for source, _ in parsed.values()), default=300)
