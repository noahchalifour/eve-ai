"""What a widget source is, and the one registry of them.

A source is an AUDITED reader: it owns its credentials, its upstream call and
its normalisation, and it is added by a reviewed code change. A recipe only
ever names a source type and its declared params, never a URL, a token, a
tool name or a member; identity comes from the authenticated route through
`ReadContext`. That is the same security boundary `recipe.py` documented for
v1, generalised so a new system costs one module instead of edits across
recipe, resolve and app.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from eve.tools_client import invoke


class SourceError(Exception):
    """A read failed. The snapshot labels the source `unavailable` and keeps
    rendering the rest; the message goes to the log only, never the client."""


@dataclass(frozen=True)
class ReadContext:
    member_sub: str
    filters: dict


def _none(_params: Any) -> frozenset[str]:
    return frozenset()


def static_permission(permission: str) -> Callable[[Any], frozenset[str]]:
    return lambda _params: frozenset({permission})


@dataclass(frozen=True)
class SourceType:
    name: str
    # A pydantic model with `extra="forbid"`: unknown keys are an error, not
    # ignored, which is what keeps `url`/`token`/`member_sub` out.
    params: type[BaseModel]
    # Top-level keys of the dict `read` returns. A template may bind
    # `$data.<alias>.<field>` only for these.
    fields: frozenset[str]
    read: Callable[[ReadContext, Any], Awaitable[dict]]
    ttl_seconds: int = 300
    # List-valued fields a template may `repeat` over, and the keys each item
    # carries (used to validate `$item.<key>`). Empty set = keys not checked.
    item_fields: Mapping[str, frozenset[str]] = field(default_factory=dict)
    permissions: Callable[[Any], frozenset[str]] = _none
    # The action targets this source exposes (e.g. entity ids). An action may
    # only ever run against a target some source of the SAME widget declared.
    targets: Callable[[Any], frozenset[str]] = _none
    description: str = ""


REGISTRY: dict[str, SourceType] = {}


def register(source: SourceType) -> SourceType:
    if source.name in REGISTRY:
        raise ValueError(f"source {source.name!r} registered twice")
    REGISTRY[source.name] = source
    return source


def parse(spec: object) -> tuple[SourceType, BaseModel] | str:
    """The source type and its validated params, or a message a model can
    act on."""
    if not isinstance(spec, dict):
        return "a source must be an object with a `type`"
    source = REGISTRY.get(spec.get("type"))  # type: ignore[arg-type]
    if source is None:
        return f"unknown source type {spec.get('type')!r}"
    try:
        params = source.params.model_validate(
            {key: value for key, value in spec.items() if key != "type"}
        )
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in error['loc']) or 'params'}: {error['msg']}"
            for error in exc.errors()
        )
        return f"source {source.name!r} params rejected: {problems}"
    return source, params


async def call_tool(tool: str, arguments: dict) -> dict:
    """`invoke` answers a JSON string or an `error: ...` string (it is built
    for model-facing callers). A source needs structure, so anything else is a
    failure."""
    raw = await invoke(tool, arguments)
    if raw.startswith("error:"):
        raise SourceError(f"{tool} degraded")
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise SourceError(f"{tool} returned unparseable JSON") from exc
    if not isinstance(parsed, dict):
        raise SourceError(f"{tool} returned a non-object")
    return parsed
