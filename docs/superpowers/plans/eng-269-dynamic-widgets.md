# Dynamic Widgets Implementation Plan (ENG-269)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn saved widgets from static charts into live, interactive, model-authored surfaces (weather, Home Assistant entities, media controls, calendar, health, anything the server has an audited source for) that refresh on a per-source interval and can run audited actions.

**Architecture:** The server (`eve-ai`) gains two registries: **sources** (audited readers that own credentials and normalisation, one file each) and **actions** (audited writes, each with a risk class, a permission and a target check). A widget recipe becomes `{version: 2, sources: {alias: spec}, template: [components]}`: the model authors a template from the existing `assistant-ui/1.0` catalog with `$data.<alias>.<field>` bindings once, and every refresh resolves the sources and emits the template with `view.data` filled in, with no model call. Presets (weather, entity, glance, media, chart) are templates the server ships. The client (`open-assistant`) stays provider-agnostic: it renders templates it already knows how to render, routes any namespaced widget action to the resource API, honours the advertised risk, polls on the advertised interval, and lets a `card` be the whole tap target.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, httpx, respx, pytest (`asyncio_mode = "auto"`), Aegra/LangGraph (eve-ai). Flutter/Dart, provider, fake_async, `package:http/testing.dart`, lucide_icons_flutter (open-assistant).

**Spec:** Linear [ENG-269](https://linear.app/chalifour-development/issue/ENG-269/widgets-need-to-be-more-dynamic) (description is the approved design). Follow-ups out of scope: ENG-270 (native home-screen widgets), ENG-271 (push updates).

## Global Constraints

* Two repos, two sets of commits: server work in `eve-ai/` (`~/GitHub/eve-ai`), client work in `open-assistant/` (`~/GitHub/open-assistant/flutter-open-assistant`). Branch `chalifournoah/eng-269-widgets-need-to-be-more-dynamic` in each.
* A widget refresh makes **no model call**. Nothing under `eve/widgets/` may import the graph or an LLM client.
* The client must not assume Eve or Home Assistant. It renders templates and runs advertised actions; every Eve-specific name lives server-side.
* `eve-ai/src/eve/ui/protocol.py` and `open-assistant/lib/domain/models/dynamic_ui/dynamic_surface_protocol.dart` must agree. The shared case file `widget_surface_cases.json` exists byte-identical in both repos and is tested on both sides.
* Unknown risk on the client defaults to `confirm`. Risk is UX friction; **security is the permission check plus the target check on the server**.
* A widget action's target must be one the widget's own sources declared (a light widget can never unlock a door, even from a tampered client).
* Resource-level authorization rule unchanged: every store query carries `member_sub`; absent and foreign ids are both 404.
* Server limits: `MAX_SOURCES = 4`, `MAX_RECIPE_BYTES = 16_384`, repeat `limit` 1..20, entity list 1..12, snapshot must pass `protocol.validate_operation(..., widget=True)` (48 KiB definition ceiling).
* Client polling: floor 5 s, default 300 s when a snapshot names no interval, error backoff doubles up to 300 s, polling only while `/widgets` is visible and the app is in the foreground.
* UX rules (apply to every UI task): 48 dp minimum tap targets; every tappable thing has a `Semantics` button role and a label; keyboard/switch access via focus + activate; state is never carried by colour alone; honour `MediaQuery.disableAnimationsOf`; never show optimistic state for a physical device (show pending, then the server's truth).
* No new dependencies on either side.

## Extension points (why this stays expandable)

| To add... | You touch | Client change? |
| -- | -- | -- |
| A data source (e.g. Monarch budgets, Immich album count) | one file `eve-ai/src/eve/widgets/sources/<name>.py` + one import line in `sources/__init__.py` + its test | No |
| An action (e.g. `home.scene.activate`) | one `register(ActionType(...))` in `eve-ai/src/eve/widgets/actions/<area>.py` + test | No (risk and label are advertised) |
| A preset | one function in `eve-ai/src/eve/widgets/presets.py` + test | No |
| An icon name | `eve/widgets/icons.py`, `AppIcons`, `DynamicSurfaceCatalog.icons`, icon vocabulary test | Yes (closed glyph set, deliberate) |
| A catalog component | both protocol validators + `widget_surface_cases.json` + `DynamicSurfaceView` | Yes (protocol change) |

The `save_widget` tool description and `/capabilities` are **generated from the registries**, so a new source or action is documented to the model and the client the moment it is registered.

## File map

**eve-ai (create)**

* `src/eve/widgets/sources/__init__.py`: imports every source module so they register.
* `src/eve/widgets/sources/base.py`: `SourceType`, `ReadContext`, `SourceError`, `REGISTRY`, `register`, `call_tool`.
* `src/eve/widgets/sources/series.py`: the v1 chart reader (records + health points), including the moved v1 validator and the health unwrap fix.
* `src/eve/widgets/sources/health.py`, `weather.py`, `home.py` (`home.entity`, `home.media`), `calendar.py`.
* `src/eve/widgets/actions/__init__.py`, `actions/base.py`, `actions/filters.py`, `actions/home.py`.
* `src/eve/widgets/template.py`: template validation and rendering (bindings, repeat, missing-binding fill).
* `src/eve/widgets/presets.py`: `chart`, `weather`, `entity`, `glance`, `media`.
* `src/eve/widgets/icons.py`: the icon-name vocabulary (mirrors the client).
* `src/eve_tools/weather.py`: Open-Meteo client, registered as `home.weather`.
* `skills/build-a-widget/SKILL.md`: authoring judgement for the model.
* `tests/fixtures/widget_surface_cases.json`, and tests listed per task.

**eve-ai (modify)**

* `src/eve/widgets/recipe.py` (v2 + upgrade), `resolve.py` (registry-driven), `app.py` (capabilities, action dispatch), `tools.py` (`save_widget` v2).
* `src/eve/ui/protocol.py` (widget-mode card action, namespaced action ids, nested-interactive rule).
* `src/eve_tools/app.py`, `src/eve_tools/settings.py`, `src/eve/settings.py`, `.env.example`.
* `docs/architecture.md`.

**open-assistant (create)**

* `test/fixtures/dynamic_ui/widget_surface_cases.json` (byte copy), `test/domain/models/dynamic_ui/widget_surface_cases_test.dart`.
* `lib/ui/features/widgets/views/widget_columns.dart` (masonry layout).

**open-assistant (modify)**

* `lib/domain/models/dynamic_ui/dynamic_surface_protocol.dart`.
* `packages/app_ui/lib/src/components/dynamic_ui/dynamic_surface_view.dart`, `dynamic_surface_catalog.dart`, `packages/app_ui/lib/src/theme/app_icons.dart` (+ tests), `tool/design_gallery/lib/src/dynamic_ui.dart`.
* `lib/domain/models/widgets/widget_resource.dart`, `lib/data/services/agent/widget_service.dart`, `langgraph_widget_service.dart`.
* `lib/ui/features/widgets/view_models/widgets_view_model.dart`, `views/widget_card.dart`, `views/widgets_screen.dart`.
* `docs/internals/widgets.md`, `AGENTS.md`.

## Wire contract (target state)

Snapshot (`GET .../resources/{id}/snapshot` and every action response):

```json
{
  "resourceId": "…", "kind": "entity", "revision": 4,
  "generatedAt": "2026-09-28T17:00:00+00:00",
  "filters": {},
  "refreshAfterSeconds": 10,
  "actionRisk": {"home.toggle:lock.front_door": "confirm"},
  "view": {"components": [ … ], "data": {"kitchen": {…}, "widget": {…}}},
  "sources": {"partial": false, "errors": []}
}
```

Action request (unchanged shape): `{"type": "home.toggle", "input": {"target": "light.kitchen"}, "expectedRevision": 4}`. `filters.replace` keeps `input` = the whole filter map.

Capabilities:

```json
{
  "protocol": "provider-resource/1.0",
  "kinds": ["chart", "custom", "entity", "glance", "media", "weather"],
  "sourceTypes": ["calendar", "health", "home.entity", "home.media", "series", "weather"],
  "actions": [{"type": "home.toggle", "risk": "safe", "label": "Toggle"}, …],
  "limits": {"maxDays": 3650, "minRefreshSeconds": 5}
}
```

HTTP errors from the action route: 400 unknown action / target / bad input, 403 permission, 404 absent or foreign, 409 revision conflict (body = current snapshot), 502 upstream (Home Assistant) failed.

---

# Part A: Server (`eve-ai`)

Run everything from `~/GitHub/eve-ai`. Test command: `uv run pytest <path> -q`.

### Task A1: Source registry, and `series` + `health` sources (fixes the health unwrap bug)

**Files:**

* Create: `src/eve/widgets/sources/__init__.py`, `src/eve/widgets/sources/base.py`, `src/eve/widgets/sources/series.py`, `src/eve/widgets/sources/health.py`
* Test: `tests/test_widgets_sources.py`, `tests/test_widgets_source_series.py`, `tests/test_widgets_source_health.py`

**Interfaces:**

- Produces:
  * `eve.widgets.sources.base.ReadContext(member_sub: str, filters: dict)` (frozen dataclass)
  * `eve.widgets.sources.base.SourceType(name, params: type[BaseModel], fields: frozenset[str], read: Callable[[ReadContext, BaseModel], Awaitable[dict]], ttl_seconds: int = 300, item_fields: Mapping[str, frozenset[str]] = {}, permissions: Callable[[BaseModel], frozenset[str]] = none, targets: Callable[[BaseModel], frozenset[str]] = none, description: str = "")`
  * `SourceError(Exception)`, `REGISTRY: dict[str, SourceType]`, `register(source) -> SourceType`, `parse(spec: object) -> tuple[SourceType, BaseModel] | str`, `async call_tool(tool: str, arguments: dict) -> dict`
  * Sources registered: `series` (fields `points`, `note`), `health` (fields `latest`, `items`)
  * `eve.widgets.sources.series.validate_v1(candidate) -> str | None` (the old `recipe.validate`, codes unchanged)
- [ ] **Step 1: Write the failing registry test**

```python
# tests/test_widgets_sources.py
"""The registry is the whole extension story: a source is one module that
calls `register`, and nothing else in the server has to learn its name."""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict


def test_every_shipped_source_is_registered():
    from eve.widgets import sources

    assert {"series", "health"} <= set(sources.REGISTRY)


def test_parse_rejects_an_unknown_type():
    from eve.widgets.sources import base

    assert base.parse({"type": "nope"}) == "unknown source type 'nope'"


def test_parse_rejects_an_unknown_param_rather_than_ignoring_it():
    """`url`, `token`, `member_sub` must never ride along silently."""
    from eve.widgets.sources import base

    error = base.parse({"type": "health", "metric": "sleep", "url": "http://x"})
    assert isinstance(error, str) and "url" in error


def test_parse_returns_the_type_and_validated_params():
    from eve.widgets.sources import base

    source, params = base.parse({"type": "health", "metric": "sleep"})
    assert source.name == "health"
    assert params.metric == "sleep"


def test_register_refuses_a_duplicate_name():
    from eve.widgets.sources import base

    class P(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def read(ctx, params):
        return {}

    with pytest.raises(ValueError):
        base.register(base.SourceType(name="health", params=P, fields=frozenset(), read=read))


async def test_call_tool_raises_on_a_degraded_call(monkeypatch):
    from eve.widgets.sources import base

    async def fake_invoke(tool, arguments):
        return "error: eve-tools unavailable (ConnectError)"

    monkeypatch.setattr(base, "invoke", fake_invoke)
    with pytest.raises(base.SourceError):
        await base.call_tool("home.get_state", {"entity_id": "light.kitchen"})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_widgets_sources.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.widgets.sources'`

- [ ] **Step 3: Implement** `base.py`

```python
# src/eve/widgets/sources/base.py
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
```

- [ ] **Step 4: Implement** `series.py` **(move the v1 logic, fix the health unwrap)**

Move `HEALTH_METRICS`, `METRIC_OPS`, `SOURCE_TYPES`, `MAX_NAME`, `MAX_SOURCES`, `_validate_source`, `_validate_metric` and the old `validate` body verbatim from `recipe.py` into this module as `validate_v1`, and move `_points_from_records`, `_points_from_health`, `_apply` verbatim from `resolve.py`. Then add:

```python
# src/eve/widgets/sources/series.py  (after the moved helpers)
from pydantic import BaseModel, ConfigDict, model_validator

from eve.records import store as record_store
from eve.widgets.sources.base import ReadContext, SourceType, call_tool, register

DEFAULT_DAYS = 30
MAX_POINTS = 180


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
        from eve.widgets.sources.base import SourceError
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
```

(Keep the imports `datetime, timedelta, timezone` at the top of the module.)

- [ ] **Step 5: Implement** `health.py`

```python
# src/eve/widgets/sources/health.py
"""Latest health entries (recovery, sleep, activity), for a stat tile."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from eve.widgets.sources.base import ReadContext, SourceError, SourceType, call_tool, register, static_permission


class HealthParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: Literal["recovery", "sleep", "activity"]
    days: int = Field(default=7, ge=1, le=14)


async def _read(ctx: ReadContext, params: HealthParams) -> dict:
    body = await call_tool(
        f"health.get_{params.metric}", {"member_sub": ctx.member_sub, "days": params.days}
    )
    items = body.get(params.metric)
    if not isinstance(items, list):
        raise SourceError(f"health.get_{params.metric} had no list")
    items = [item for item in items if isinstance(item, dict)]
    # Newest first is the tool's contract; `latest` is the tile's headline.
    return {"latest": items[0] if items else {}, "items": items}


register(SourceType(
    name="health",
    params=HealthParams,
    fields=frozenset({"latest", "items"}),
    read=_read,
    ttl_seconds=900,
    item_fields={"items": frozenset()},
    permissions=static_permission("health"),
    description="Recent health entries, newest first. Params: metric (recovery|sleep|activity), days (1-14). "
    "Fields: latest (newest entry), items (list). Entry keys depend on the provider, e.g. date, score_0_100.",
))
```

- [ ] **Step 6: Create the package init**

```python
# src/eve/widgets/sources/__init__.py
"""Importing this package registers every source. Adding a source = a new
module here plus one import line below (and its test)."""
from eve.widgets.sources.base import REGISTRY, SourceError, SourceType, parse  # noqa: F401
from eve.widgets.sources import series, health  # noqa: E402,F401
```

- [ ] **Step 7: Write the series/health tests**

```python
# tests/test_widgets_source_series.py
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


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
```

```python
# tests/test_widgets_source_health.py
from __future__ import annotations

import json


async def test_health_latest_is_the_newest_entry(monkeypatch):
    from eve.widgets.sources import base, health

    async def fake_invoke(tool, arguments):
        return json.dumps({"sleep": [{"date": "2026-09-28"}, {"date": "2026-09-27"}]})

    monkeypatch.setattr(base, "invoke", fake_invoke)
    out = await health._read(base.ReadContext("sub-noah", {}), health.HealthParams(metric="sleep"))
    assert out["latest"] == {"date": "2026-09-28"}
    assert len(out["items"]) == 2


def test_health_requires_the_health_permission():
    from eve.widgets.sources import REGISTRY
    from eve.widgets.sources.health import HealthParams

    assert REGISTRY["health"].permissions(HealthParams(metric="sleep")) == {"health"}
```

Move every test in `tests/test_widgets_recipe.py` that asserts a v1 code into `tests/test_widgets_source_series.py`, calling `validate_v1` instead of `recipe.validate` (Task A6 rewrites the rest of that file).

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/test_widgets_sources.py tests/test_widgets_source_series.py tests/test_widgets_source_health.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/eve/widgets/sources tests/test_widgets_sources.py tests/test_widgets_source_series.py tests/test_widgets_source_health.py
git commit -m "feat(widgets): source registry with series and health sources (ENG-269)

Also fixes health widget sources always reporting unavailable: health.get_*
returns a keyed dict, the v1 reader expected a bare list."
```

(`recipe.py`/`resolve.py` still hold their own copies until A6/A7 switch over; that is fine for one commit.)

---

### Task A2: Weather (eve-tools `home.weather` + `weather` source), fixing the stylist

**Files:**

* Create: `src/eve_tools/weather.py`, `src/eve/widgets/sources/weather.py`, `src/eve/widgets/icons.py`
* Modify: `src/eve_tools/settings.py`, `src/eve_tools/app.py:34` (`_HANDLERS`), `src/eve/widgets/sources/__init__.py`, `.env.example`
* Test: `tests/test_eve_tools_weather.py`, `tests/test_widgets_source_weather.py`

**Interfaces:**

- Consumes: `SourceType`, `register`, `call_tool` (A1).
- Produces:
  * eve-tools tool `home.weather` with args `{"days": 1..7}` returning `{"units": "metric"|"imperial", "current": {"temperature", "apparent_temperature", "humidity", "wind_speed", "code", "is_day"}, "daily": [{"date", "high", "low", "code", "precipitation_chance"}]}` (numbers raw).
  * `eve.widgets.icons.ICON_NAMES: frozenset[str]`, `eve.widgets.icons.weather_icon(code: int, is_day: bool) -> str`, `weather_condition(code: int) -> str`.
  * Source `weather` with fields `current`, `today`, `days`.
- [ ] **Step 1: Add settings and the failing eve-tools test**

In `src/eve_tools/settings.py` add to `ToolsSettings` (after `home_assistant_token`):

```python
    # The household's location for `home.weather` (Open-Meteo, no key needed).
    # Unset means the tool answers an error, so the stylist says it cannot see
    # the weather instead of inventing one.
    weather_latitude: float | None = None
    weather_longitude: float | None = None
    weather_units: str = "metric"  # or "imperial"
```

In `.env.example` under the Home Assistant lines add:

```
EVE_TOOLS_WEATHER_LATITUDE=49.28
EVE_TOOLS_WEATHER_LONGITUDE=-123.12
EVE_TOOLS_WEATHER_UNITS=metric
```

```python
# tests/test_eve_tools_weather.py
import httpx
import pytest
import respx

from eve_tools import weather


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    from eve_tools.settings import get_tools_settings

    monkeypatch.setenv("EVE_TOOLS_WEATHER_LATITUDE", "49.0")
    monkeypatch.setenv("EVE_TOOLS_WEATHER_LONGITUDE", "-123.0")
    get_tools_settings.cache_clear()
    yield
    get_tools_settings.cache_clear()


@respx.mock
async def test_weather_normalises_open_meteo():
    respx.get("https://api.open-meteo.com/v1/forecast").mock(return_value=httpx.Response(200, json={
        "current": {"temperature_2m": 12.4, "apparent_temperature": 10.1, "relative_humidity_2m": 80,
                    "wind_speed_10m": 14.0, "weather_code": 61, "is_day": 1},
        "daily": {"time": ["2026-09-28"], "temperature_2m_max": [14.2], "temperature_2m_min": [8.0],
                  "weather_code": [61], "precipitation_probability_max": [70]},
    }))
    out = await weather.forecast(days=1)
    assert out["current"]["temperature"] == 12.4
    assert out["daily"] == [{"date": "2026-09-28", "high": 14.2, "low": 8.0, "code": 61, "precipitation_chance": 70}]
    assert out["units"] == "metric"


async def test_weather_without_a_location_fails_loudly(monkeypatch):
    from eve_tools.settings import get_tools_settings

    monkeypatch.delenv("EVE_TOOLS_WEATHER_LATITUDE")
    get_tools_settings.cache_clear()
    with pytest.raises(RuntimeError, match="location"):
        await weather.forecast(days=1)


def test_home_weather_is_a_registered_tool():
    """The stylist has called `home.weather` since it shipped; nothing handled
    it, so it has always answered 404 (found while planning ENG-269)."""
    from eve_tools.app import _HANDLERS

    assert "home.weather" in _HANDLERS
```

Run: `uv run pytest tests/test_eve_tools_weather.py -q` → FAIL (`cannot import name 'weather'`).

- [ ] **Step 2: Implement** `eve_tools/weather.py` **and register it**

```python
# src/eve_tools/weather.py
"""Household weather from Open-Meteo: no key, no account, one GET."""
from __future__ import annotations

import httpx

from eve_tools.settings import get_tools_settings

_URL = "https://api.open-meteo.com/v1/forecast"


async def forecast(days: int = 3) -> dict:
    settings = get_tools_settings()
    if settings.weather_latitude is None or settings.weather_longitude is None:
        raise RuntimeError("weather location is not configured")
    imperial = settings.weather_units == "imperial"
    params = {
        "latitude": settings.weather_latitude,
        "longitude": settings.weather_longitude,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": max(1, min(7, int(days))),
        "temperature_unit": "fahrenheit" if imperial else "celsius",
        "wind_speed_unit": "mph" if imperial else "kmh",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(_URL, params=params)
        response.raise_for_status()
        body = response.json()
    current, daily = body["current"], body["daily"]
    return {
        "units": "imperial" if imperial else "metric",
        "current": {
            "temperature": current["temperature_2m"],
            "apparent_temperature": current["apparent_temperature"],
            "humidity": current["relative_humidity_2m"],
            "wind_speed": current["wind_speed_10m"],
            "code": current["weather_code"],
            "is_day": bool(current["is_day"]),
        },
        "daily": [
            {
                "date": date,
                "high": daily["temperature_2m_max"][i],
                "low": daily["temperature_2m_min"][i],
                "code": daily["weather_code"][i],
                "precipitation_chance": daily["precipitation_probability_max"][i],
            }
            for i, date in enumerate(daily["time"])
        ],
    }
```

In `src/eve_tools/app.py`: add `weather` to the `from eve_tools import ...` line and add to `_HANDLERS`:

```python
    "home.weather": lambda a: weather.forecast(a.get("days", 3)),
```

Run: `uv run pytest tests/test_eve_tools_weather.py -q` → PASS.

- [ ] **Step 3: Write the icon vocabulary**

```python
# src/eve/widgets/icons.py
"""Icon names a widget may emit. Mirrors the client's closed glyph map
(`DynamicSurfaceCatalog.icons` in open-assistant's app_ui); an unknown name
renders the client's `unknown` glyph, never an error. Keep both lists in step
by hand when adding a glyph."""
from __future__ import annotations

ICON_NAMES = frozenset({
    "info", "alert", "check",
    "sun", "moon", "cloud", "cloud-sun", "rain", "snow", "storm", "fog", "wind",
    "thermometer", "droplets",
    "lightbulb", "lightbulb-off", "power", "lock", "unlock", "fan", "plug", "home",
    "music", "play", "pause", "skip-forward", "skip-back", "volume-down", "volume-up",
    "calendar", "heart",
})


def weather_condition(code: int) -> str:
    """WMO weather code to a short phrase (Open-Meteo uses WMO codes)."""
    if code == 0:
        return "Clear"
    if code in (1, 2):
        return "Partly cloudy"
    if code == 3:
        return "Overcast"
    if code in (45, 48):
        return "Fog"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "Rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "Snow"
    if 95 <= code <= 99:
        return "Thunderstorm"
    return "Unknown"


def weather_icon(code: int, is_day: bool = True) -> str:
    return {
        "Clear": "sun" if is_day else "moon",
        "Partly cloudy": "cloud-sun" if is_day else "cloud",
        "Overcast": "cloud",
        "Fog": "fog",
        "Rain": "rain",
        "Snow": "snow",
        "Thunderstorm": "storm",
    }.get(weather_condition(code), "info")
```

- [ ] **Step 4: Write the failing weather source test**

```python
# tests/test_widgets_source_weather.py
from __future__ import annotations

import json

FORECAST = {
    "units": "metric",
    "current": {"temperature": 12.4, "apparent_temperature": 10.1, "humidity": 80,
                "wind_speed": 14.0, "code": 61, "is_day": True},
    "daily": [
        {"date": "2026-09-28", "high": 14.2, "low": 8.0, "code": 61, "precipitation_chance": 70},
        {"date": "2026-09-29", "high": 16.0, "low": 9.0, "code": 1, "precipitation_chance": 10},
    ],
}


async def test_weather_formats_for_display(monkeypatch):
    from eve.widgets.sources import base, weather

    async def fake_invoke(tool, arguments):
        assert tool == "home.weather" and arguments == {"days": 2}
        return json.dumps(FORECAST)

    monkeypatch.setattr(base, "invoke", fake_invoke)
    out = await weather._read(base.ReadContext("sub-noah", {}), weather.WeatherParams(days=2))

    assert out["current"] == {
        "temperature": "12°", "feels_like": "Feels like 10°", "condition": "Rain",
        "icon": "rain", "humidity": "80%", "wind": "14 km/h",
    }
    assert out["days"][1] == {"day": "Tue", "high": "16°", "low": "9°", "range": "16° / 9°",
                              "condition": "Partly cloudy", "icon": "cloud-sun", "precipitation": "10%"}
    assert out["today"] == out["days"][0]
```

Run: `uv run pytest tests/test_widgets_source_weather.py -q` → FAIL (no module).

- [ ] **Step 5: Implement the weather source**

```python
# src/eve/widgets/sources/weather.py
"""Household weather, formatted for display. Formatting lives here, not in
eve-tools: the stylist's model wants raw numbers, a tile wants `12°`."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from eve.widgets.icons import weather_condition, weather_icon
from eve.widgets.sources.base import ReadContext, SourceType, call_tool, register


class WeatherParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(default=5, ge=1, le=7)


def _deg(value: float) -> str:
    return f"{round(value)}°"


async def _read(ctx: ReadContext, params: WeatherParams) -> dict:
    body = await call_tool("home.weather", {"days": params.days})
    wind_unit = "mph" if body.get("units") == "imperial" else "km/h"
    now = body["current"]
    days = [
        {
            "day": date.fromisoformat(entry["date"]).strftime("%a"),
            "high": _deg(entry["high"]),
            "low": _deg(entry["low"]),
            "range": f"{_deg(entry['high'])} / {_deg(entry['low'])}",
            "condition": weather_condition(entry["code"]),
            "icon": weather_icon(entry["code"]),
            "precipitation": f"{entry['precipitation_chance']}%",
        }
        for entry in body["daily"]
    ]
    return {
        "current": {
            "temperature": _deg(now["temperature"]),
            "feels_like": f"Feels like {_deg(now['apparent_temperature'])}",
            "condition": weather_condition(now["code"]),
            "icon": weather_icon(now["code"], now["is_day"]),
            "humidity": f"{now['humidity']}%",
            "wind": f"{round(now['wind_speed'])} {wind_unit}",
        },
        "today": days[0] if days else {},
        "days": days,
    }


_DAY_KEYS = frozenset({"day", "high", "low", "range", "condition", "icon", "precipitation"})

register(SourceType(
    name="weather",
    params=WeatherParams,
    fields=frozenset({"current", "today", "days"}),
    read=_read,
    ttl_seconds=900,
    item_fields={"days": _DAY_KEYS},
    description="Household weather. Params: days (1-7). Fields: current {temperature, feels_like, condition, icon, "
    "humidity, wind}; today and each of days {day, high, low, range, condition, icon, precipitation}.",
))
```

Add `weather` to the import line in `sources/__init__.py`.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_eve_tools_weather.py tests/test_widgets_source_weather.py tests/test_widgets_sources.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/eve_tools/weather.py src/eve_tools/app.py src/eve_tools/settings.py .env.example \
  src/eve/widgets/icons.py src/eve/widgets/sources tests/test_eve_tools_weather.py tests/test_widgets_source_weather.py
git commit -m "feat(widgets): weather source backed by a real home.weather tool (ENG-269)

home.weather was called by the stylist but never handled by eve-tools."
```

---

### Task A3: Home Assistant sources (`home.entity`, `home.media`)

**Files:**

* Create: `src/eve/widgets/sources/home.py`
* Modify: `src/eve/widgets/sources/__init__.py`
* Test: `tests/test_widgets_source_home.py`

**Interfaces:**

- Consumes: A1 registry, `icons.ICON_NAMES`.
- Produces:
  * `home.entity` params `{entities: list[str] (1..12, each "domain.object_id")}`; fields `primary`, `items`; item keys `entity_id, name, domain, state, state_label, on, icon, value`; `targets` = the entity ids; `ttl_seconds=10`; permission `home.control`.
  * `home.media` params `{entity: "media_player.*"}`; field `player` with keys `entity_id, name, state_label, playing, title, artist, volume, play_icon, play_label`; `targets` = `{entity}`; `ttl_seconds=10`; permission `home.control`.
  * `eve.widgets.sources.home.normalise_entity(state: dict) -> dict` (reused by actions tests).
- [ ] **Step 1: Write the failing tests**

```python
# tests/test_widgets_source_home.py
from __future__ import annotations

import json

import pytest

STATES = {
    "light.kitchen": {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen"}},
    "lock.front_door": {"entity_id": "lock.front_door", "state": "locked", "attributes": {"friendly_name": "Front door"}},
    "sensor.outside": {"entity_id": "sensor.outside", "state": "11.4",
                       "attributes": {"friendly_name": "Outside", "unit_of_measurement": "°C", "device_class": "temperature"}},
    "media_player.living_room": {"entity_id": "media_player.living_room", "state": "playing", "attributes": {
        "friendly_name": "Living room", "media_title": "Blue in Green", "media_artist": "Miles Davis", "volume_level": 0.4}},
}


@pytest.fixture
def ha(monkeypatch):
    from eve.widgets.sources import base

    async def fake_invoke(tool, arguments):
        assert tool == "home.get_state"
        return json.dumps(STATES[arguments["entity_id"]])

    monkeypatch.setattr(base, "invoke", fake_invoke)


async def test_entity_items_are_normalised_for_display(ha):
    from eve.widgets.sources import base, home

    params = home.EntityParams(entities=["light.kitchen", "lock.front_door", "sensor.outside"])
    out = await home._read_entities(base.ReadContext("sub-noah", {}), params)

    assert out["primary"] == {"entity_id": "light.kitchen", "name": "Kitchen", "domain": "light", "state": "on",
                              "state_label": "On", "on": True, "icon": "lightbulb", "value": "On"}
    assert out["items"][1]["icon"] == "lock" and out["items"][1]["state_label"] == "Locked"
    assert out["items"][2]["value"] == "11.4 °C" and out["items"][2]["icon"] == "thermometer"


async def test_one_bad_entity_does_not_blank_the_rest(monkeypatch):
    from eve.widgets.sources import base, home

    async def flaky(tool, arguments):
        if arguments["entity_id"] == "light.gone":
            return "error: 404"
        return json.dumps(STATES[arguments["entity_id"]])

    monkeypatch.setattr(base, "invoke", flaky)
    out = await home._read_entities(base.ReadContext("s", {}), home.EntityParams(entities=["light.gone", "light.kitchen"]))
    assert out["items"][0]["state_label"] == "Unavailable"
    assert out["items"][1]["name"] == "Kitchen"


def test_entity_ids_are_validated_and_become_targets():
    from pydantic import ValidationError

    from eve.widgets.sources import REGISTRY, home

    with pytest.raises(ValidationError):
        home.EntityParams(entities=["http://evil"])
    params = home.EntityParams(entities=["light.kitchen"])
    assert REGISTRY["home.entity"].targets(params) == {"light.kitchen"}
    assert REGISTRY["home.entity"].permissions(params) == {"home.control"}


async def test_media_player_reads_now_playing(ha):
    from eve.widgets.sources import base, home

    out = await home._read_media(base.ReadContext("s", {}), home.MediaParams(entity="media_player.living_room"))
    assert out["player"] == {"entity_id": "media_player.living_room", "name": "Living room", "state_label": "Playing",
                             "playing": True, "title": "Blue in Green", "artist": "Miles Davis", "volume": "40%",
                             "play_icon": "pause", "play_label": "Pause"}


def test_media_only_accepts_media_players():
    from pydantic import ValidationError

    from eve.widgets.sources import home

    with pytest.raises(ValidationError):
        home.MediaParams(entity="light.kitchen")


def test_every_emitted_icon_is_in_the_vocabulary():
    from eve.widgets import icons
    from eve.widgets.sources import home

    assert set(home._DOMAIN_ICONS.values()) | set(home._OFF_ICONS.values()) <= icons.ICON_NAMES
```

Run: `uv run pytest tests/test_widgets_source_home.py -q` → FAIL.

- [ ] **Step 2: Implement** `sources/home.py`

```python
# src/eve/widgets/sources/home.py
"""Home Assistant entity state, normalised for tiles.

Reads one `home.get_state` per entity, concurrently, so one missing entity
degrades to an `Unavailable` row instead of failing the widget. Every entity
id a widget reads is also a TARGET: the only things a widget's actions may
touch (see `eve.widgets.actions`).
"""
from __future__ import annotations

import asyncio
import logging
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from eve.widgets.sources.base import ReadContext, SourceError, SourceType, call_tool, register, static_permission

logger = logging.getLogger(__name__)

_ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_ON_STATES = frozenset({"on", "open", "unlocked", "playing", "home", "heat", "cool"})

_DOMAIN_ICONS = {
    "light": "lightbulb", "switch": "power", "input_boolean": "power", "fan": "fan",
    "lock": "lock", "cover": "home", "media_player": "music", "climate": "thermometer",
    "binary_sensor": "info", "sensor": "info", "plug": "plug",
}
_OFF_ICONS = {"light": "lightbulb-off"}
_DEVICE_CLASS_ICONS = {"temperature": "thermometer", "humidity": "droplets"}


def _check_entity(value: str) -> str:
    if not _ENTITY_ID.match(value):
        raise ValueError(f"{value!r} is not a Home Assistant entity id")
    return value


class EntityParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: list[str] = Field(min_length=1, max_length=12)

    @field_validator("entities")
    @classmethod
    def _ids(cls, value: list[str]) -> list[str]:
        return [_check_entity(entity) for entity in value]


class MediaParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str

    @field_validator("entity")
    @classmethod
    def _player(cls, value: str) -> str:
        _check_entity(value)
        if not value.startswith("media_player."):
            raise ValueError("entity must be a media_player")
        return value


def _label(state: str) -> str:
    return state.replace("_", " ").capitalize() if state else "Unknown"


def normalise_entity(raw: dict) -> dict:
    entity_id = raw.get("entity_id", "")
    domain = entity_id.split(".", 1)[0]
    state = str(raw.get("state", "unknown"))
    attributes = raw.get("attributes") or {}
    on = state in _ON_STATES
    unit = attributes.get("unit_of_measurement")
    icon = _DEVICE_CLASS_ICONS.get(attributes.get("device_class"), _DOMAIN_ICONS.get(domain, "info"))
    if not on and domain in _OFF_ICONS:
        icon = _OFF_ICONS[domain]
    if domain == "lock" and state == "unlocked":
        icon = "unlock"
    label = _label(state)
    return {
        "entity_id": entity_id,
        "name": attributes.get("friendly_name") or entity_id,
        "domain": domain,
        "state": state,
        "state_label": label,
        "on": on,
        "icon": icon,
        "value": f"{state} {unit}" if unit else label,
    }


def _unavailable(entity_id: str) -> dict:
    return normalise_entity({"entity_id": entity_id, "state": "unavailable"}) | {"name": entity_id}


async def _one(entity_id: str) -> dict:
    try:
        return normalise_entity(await call_tool("home.get_state", {"entity_id": entity_id}))
    except SourceError:
        logger.warning("home.get_state failed for %s", entity_id)
        return _unavailable(entity_id)


async def _read_entities(ctx: ReadContext, params: EntityParams) -> dict:
    items = list(await asyncio.gather(*(_one(entity) for entity in params.entities)))
    if all(item["state"] == "unavailable" for item in items):
        # Every read failed: that is an outage, not a house full of broken
        # devices, and the card should say so via `sources.errors`.
        raise SourceError("no entity could be read")
    return {"primary": items[0], "items": items}


async def _read_media(ctx: ReadContext, params: MediaParams) -> dict:
    raw = await call_tool("home.get_state", {"entity_id": params.entity})
    base = normalise_entity(raw)
    attributes = raw.get("attributes") or {}
    playing = base["state"] == "playing"
    volume = attributes.get("volume_level")
    return {"player": {
        "entity_id": base["entity_id"],
        "name": base["name"],
        "state_label": base["state_label"],
        "playing": playing,
        "title": attributes.get("media_title") or "Nothing playing",
        "artist": attributes.get("media_artist") or "",
        "volume": f"{round(volume * 100)}%" if isinstance(volume, (int, float)) else "",
        "play_icon": "pause" if playing else "play",
        "play_label": "Pause" if playing else "Play",
    }}


_ITEM_KEYS = frozenset({"entity_id", "name", "domain", "state", "state_label", "on", "icon", "value"})

register(SourceType(
    name="home.entity",
    params=EntityParams,
    fields=frozenset({"primary", "items"}),
    read=_read_entities,
    ttl_seconds=10,
    item_fields={"items": _ITEM_KEYS},
    permissions=static_permission("home.control"),
    targets=lambda params: frozenset(params.entities),
    description="Home Assistant entity state. Params: entities (1-12 entity ids). Fields: primary (first entity), "
    "items (all). Each has entity_id, name, domain, state, state_label, on, icon, value.",
))

register(SourceType(
    name="home.media",
    params=MediaParams,
    fields=frozenset({"player"}),
    read=_read_media,
    ttl_seconds=10,
    permissions=static_permission("home.control"),
    targets=lambda params: frozenset({params.entity}),
    description="A Home Assistant media player. Params: entity (media_player.*). Field: player {entity_id, name, "
    "state_label, playing, title, artist, volume, play_icon, play_label}.",
))
```

Add `home` to the import line in `sources/__init__.py`.

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_widgets_source_home.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/eve/widgets/sources tests/test_widgets_source_home.py
git commit -m "feat(widgets): Home Assistant entity and media player sources (ENG-269)"
```

---

### Task A4: Calendar source

**Files:**

* Create: `src/eve/widgets/sources/calendar.py`
* Modify: `src/eve/settings.py` (add `household_timezone`), `.env.example`, `src/eve/widgets/sources/__init__.py`
* Test: `tests/test_widgets_source_calendar.py`

**Interfaces:**

- Produces: source `calendar`, params `{horizon_days: 1..14 = 7, limit: 1..10 = 5}`, fields `next`, `items`, `count`; item keys `summary, when, location`; ttl 300; no extra permission (reads only the member's own calendar via `member_sub`).
- [ ] **Step 1: Add the setting**

In `src/eve/settings.py` `Settings`, near the other deployment fields:

```python
    # How widgets render wall-clock times ("Mon 3:00 PM"). IANA name.
    household_timezone: str = "UTC"
```

In `.env.example`: `EVE_HOUSEHOLD_TIMEZONE=America/Vancouver`.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_widgets_source_calendar.py
from __future__ import annotations

import json


async def test_calendar_formats_local_times(monkeypatch):
    from eve.settings import get_settings
    from eve.widgets.sources import base, calendar

    monkeypatch.setenv("EVE_HOUSEHOLD_TIMEZONE", "America/Vancouver")
    get_settings.cache_clear()

    async def fake_invoke(tool, arguments):
        assert tool == "calendar.list_events"
        assert arguments == {"member_sub": "sub-noah", "lookahead_minutes": 0, "horizon_days": 7}
        return json.dumps({"events": [
            {"summary": "Dentist", "location": "Main St", "start": "2026-09-28T22:00:00+00:00", "end": None},
            {"summary": "Standup", "location": "", "start": "2026-09-29T16:30:00+00:00", "end": None},
        ]})

    monkeypatch.setattr(base, "invoke", fake_invoke)
    out = await calendar._read(base.ReadContext("sub-noah", {}), calendar.CalendarParams(limit=1))

    assert out["items"] == [{"summary": "Dentist", "when": "Mon 3:00 PM", "location": "Main St"}]
    assert out["next"] == out["items"][0]
    assert out["count"] == "2 upcoming"
    get_settings.cache_clear()


async def test_an_empty_calendar_has_a_friendly_next(monkeypatch):
    from eve.widgets.sources import base, calendar

    async def fake_invoke(tool, arguments):
        return json.dumps({"events": []})

    monkeypatch.setattr(base, "invoke", fake_invoke)
    out = await calendar._read(base.ReadContext("s", {}), calendar.CalendarParams())
    assert out["next"]["summary"] == "Nothing scheduled"
    assert out["items"] == []
```

Run → FAIL.

- [ ] **Step 3: Implement**

```python
# src/eve/widgets/sources/calendar.py
"""The member's upcoming events, soonest first."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field

from eve.settings import get_settings
from eve.widgets.sources.base import ReadContext, SourceType, call_tool, register


class CalendarParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    horizon_days: int = Field(default=7, ge=1, le=14)
    limit: int = Field(default=5, ge=1, le=10)


def _zone() -> ZoneInfo:
    try:
        return ZoneInfo(get_settings().household_timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _when(start: object, zone: ZoneInfo) -> str:
    if not isinstance(start, str):
        return ""
    moment = datetime.fromisoformat(start).astimezone(zone)
    return moment.strftime("%a %-I:%M %p")


async def _read(ctx: ReadContext, params: CalendarParams) -> dict:
    body = await call_tool("calendar.list_events", {
        "member_sub": ctx.member_sub, "lookahead_minutes": 0, "horizon_days": params.horizon_days,
    })
    events = sorted(
        (event for event in body.get("events") or [] if isinstance(event, dict)),
        key=lambda event: event.get("start") or "",
    )
    zone = _zone()
    items = [
        {"summary": event.get("summary") or "Untitled", "when": _when(event.get("start"), zone),
         "location": event.get("location") or ""}
        for event in events[: params.limit]
    ]
    return {
        "next": items[0] if items else {"summary": "Nothing scheduled", "when": "", "location": ""},
        "items": items,
        "count": f"{len(events)} upcoming",
    }


register(SourceType(
    name="calendar",
    params=CalendarParams,
    fields=frozenset({"next", "items", "count"}),
    read=_read,
    ttl_seconds=300,
    item_fields={"items": frozenset({"summary", "when", "location"})},
    description="The member's upcoming events. Params: horizon_days (1-14), limit (1-10). Fields: next, items "
    "(each {summary, when, location}), count.",
))
```

Add `calendar` to the import line in `sources/__init__.py`.

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/test_widgets_source_calendar.py -q` → PASS

```bash
git add src/eve/settings.py .env.example src/eve/widgets/sources tests/test_widgets_source_calendar.py
git commit -m "feat(widgets): calendar source (ENG-269)"
```

---

### Task A5: Protocol: widget-mode card actions, namespaced action ids, no nested interactives

**Files:**

* Modify: `src/eve/ui/protocol.py:63-72` (action ids), `:407-487` (`_validate_properties`, `_validate_property`)
* Create: `tests/fixtures/widget_surface_cases.json`
* Test: `tests/test_ui_protocol_widget.py`

**Interfaces:**

- Produces:
  * In widget mode only: `card` accepts `actionId` and `actionValue`; `actionId` accepts `surface.submit` **or** any id matching `^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$` (the server's action registry is the authority on which exist); a `card` with an `actionId` must contain no descendant with `actionId` or `setState` (`action-schema`).
  * Chat mode unchanged.
  * `tests/fixtures/widget_surface_cases.json`: `[{"name": str, "components": [...], "valid": bool}]`, copied byte-identical to the client in Task B1.
- [ ] **Step 1: Write the shared case file**

```json
[
  {"name": "tappable card", "valid": true, "components": [
    {"id": "root", "type": "card", "properties": {"title": "Kitchen", "actionId": "home.toggle", "actionValue": "light.kitchen"}, "children": [
      {"id": "t", "type": "text", "properties": {"text": "$data.kitchen.primary.state_label"}, "children": []}]}]},
  {"name": "namespaced button action", "valid": true, "components": [
    {"id": "b", "type": "button", "properties": {"label": "Next", "actionId": "home.media.next", "actionValue": "media_player.lr"}, "children": []}]},
  {"name": "range control", "valid": true, "components": [
    {"id": "r", "type": "segmentedSelection", "properties": {"options": ["7", "30"], "selected": "30", "actionId": "widget.setRange"}, "children": []}]},
  {"name": "action id without a namespace", "valid": false, "components": [
    {"id": "b", "type": "button", "properties": {"label": "Go", "actionId": "toggle"}, "children": []}]},
  {"name": "action id with uppercase", "valid": false, "components": [
    {"id": "b", "type": "button", "properties": {"label": "Go", "actionId": "Home.Toggle"}, "children": []}]},
  {"name": "tappable card containing a button", "valid": false, "components": [
    {"id": "root", "type": "card", "properties": {"actionId": "home.toggle", "actionValue": "light.kitchen"}, "children": [
      {"id": "b", "type": "button", "properties": {"label": "Off", "actionId": "home.toggle", "actionValue": "light.kitchen"}, "children": []}]}]},
  {"name": "tappable card containing a setState button", "valid": false, "components": [
    {"id": "root", "type": "card", "properties": {"actionId": "home.toggle"}, "children": [
      {"id": "b", "type": "button", "properties": {"label": "x", "setState": {"a": 1}}, "children": []}]}]},
  {"name": "card action value must be scalar", "valid": false, "components": [
    {"id": "root", "type": "card", "properties": {"actionId": "home.toggle", "actionValue": {"entity": "x"}}, "children": []}]}
]
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_ui_protocol_widget.py
"""Widget-mode validation. The case file is shared byte-for-byte with
open-assistant (test/fixtures/dynamic_ui/widget_surface_cases.json); if this
file changes, copy it there and run the Dart test too."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eve.ui import protocol

CASES = json.loads((Path(__file__).parent / "fixtures" / "widget_surface_cases.json").read_text())


def _op(components):
    return {"protocol": protocol.PROTOCOL, "op": "create", "surface": {
        "surfaceId": "widget:x", "catalogId": "column", "catalogVersion": "1",
        "components": components, "data": {}, "localState": {}}}


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_shared_widget_cases(case):
    error = protocol.validate_operation(_op(case["components"]), widget=True)
    assert (error is None) == case["valid"], error


def test_chat_mode_still_rejects_a_tappable_card():
    components = CASES[0]["components"]
    assert protocol.validate_operation(_op(components)) == "component-schema"


def test_chat_mode_still_rejects_a_namespaced_action():
    assert protocol.validate_operation(_op(CASES[1]["components"])) == "action-schema"
```

Run: `uv run pytest tests/test_ui_protocol_widget.py -q` → FAIL.

- [ ] **Step 3: Implement**

In `src/eve/ui/protocol.py` replace `_WIDGET_ACTION_IDS` with:

```python
# Widget snapshots may name any NAMESPACED action (`home.toggle`,
# `widget.setRange`, `filters.replace`...). Syntax only: which actions exist
# is the widget action registry's call (`eve.widgets.actions`), enforced by
# the resource route, so a new action needs no protocol change. Chat surfaces
# keep `ACTION_IDS` exactly, so a chat surface can never express one.
_WIDGET_ACTION_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")

# Widget mode lets a whole card be the tap target.
_WIDGET_EXTRA_PROPERTIES: dict[str, frozenset[str]] = {
    "card": frozenset({"actionId", "actionValue"}),
}
```

In `_validate_properties`, replace the `allowed = ...` line and add the nested rule:

```python
    allowed = _ALLOWED_PROPERTIES.get(component_type, frozenset())
    if widget:
        allowed = allowed | _WIDGET_EXTRA_PROPERTIES.get(component_type, frozenset())
```

`_validate_properties` only sees properties, so add the nested check in `_validate_components.visit`, right after the `_validate_properties` call:

```python
        if (
            widget
            and component["type"] == "card"
            and "actionId" in (component.get("properties") or {})
            and _has_interactive_descendant(component)
        ):
            # Two tap targets stacked on one spot: the inner one steals the
            # tap on some platforms and not others, and a screen reader
            # announces one control where there are two.
            return "action-schema"
```

and add the helper:

```python
def _has_interactive_descendant(component: dict) -> bool:
    stack = list(component.get("children") or [])
    while stack:
        child = stack.pop()
        if not isinstance(child, dict):
            continue
        properties = child.get("properties") or {}
        if isinstance(properties, dict) and ("actionId" in properties or "setState" in properties):
            return True
        stack.extend(child.get("children") or [])
    return False
```

In `_validate_property`, the `actionId` branch becomes:

```python
    if key == "actionId":
        if value in ACTION_IDS:
            return None
        if widget and isinstance(value, str) and _WIDGET_ACTION_ID.match(value):
            return None
        return "action-schema"
```

Delete `_WIDGET_ACTION_IDS` and update any reference (grep `_WIDGET_ACTION_IDS`). Keep `widgets/app.py`'s `RANGE_ACTION_ID`.

- [ ] **Step 4: Run the whole UI protocol suite**

Run: `uv run pytest tests/test_ui_protocol.py tests/test_ui_protocol_widget.py tests/test_ui_schema.py tests/test_widgets_resolve.py -q`
Expected: PASS. If an existing test asserted that widget mode rejects some other namespaced id, update it to assert the chat-mode rejection instead: the registry, not the validator, now owns that decision.

- [ ] **Step 5: Commit**

```bash
git add src/eve/ui/protocol.py tests/fixtures/widget_surface_cases.json tests/test_ui_protocol_widget.py tests/test_ui_protocol.py
git commit -m "feat(ui): widget-mode tappable cards and namespaced widget actions (ENG-269)"
```

---

### Task A6: Templates, recipe v2, presets

**Files:**

* Create: `src/eve/widgets/template.py`, `src/eve/widgets/presets.py`
* Modify: `src/eve/widgets/recipe.py` (rewrite)
* Test: `tests/test_widgets_template.py`, `tests/test_widgets_presets.py`, `tests/test_widgets_recipe.py` (rewrite)

**Interfaces:**

* Consumes: `sources.parse`, `SourceType` (A1), `protocol.validate_operation(..., widget=True)` (A5). Actions registry lookups go through a callable passed in, so this task does not depend on A7: `action_accepts: Callable[[str, str], bool]` (action id, literal target) → whether that action exists and accepts that target.
* Produces:
  * `recipe.RECIPE_VERSION = 2`, `recipe.upgrade(recipe: dict) -> dict` (v1 → chart preset), `recipe.parse_sources(recipe) -> dict[str, tuple[SourceType, BaseModel]] | str`, `recipe.validate(recipe, *, action_accepts) -> str | None` (human-readable message), `recipe.validate_filters` (unchanged), `recipe.required_permissions(recipe) -> list[str]`, `recipe.declared_targets(recipe) -> frozenset[str]`, `recipe.ttl_seconds(recipe) -> int`.
  * `template.WIDGET_FIELDS = frozenset({"title", "days", "empty_points"})`, `template.validate(components, sources, *, action_accepts) -> str | None`, `template.render(components, data) -> tuple[list, list[str]]`.
  * `presets.PRESETS: dict[str, Preset]`, `Preset(name, description, options: type[BaseModel], build: Callable[[BaseModel], dict])`, `presets.build(name, options: dict | None) -> dict | str`.

Template language (document this verbatim in the skill, Task A8):

- Bindings: `$data.<alias>.<field>[.<key>...]` where alias is a declared source or `widget` (`$data.widget.title`, `$data.widget.days`).
- Repeat: a `list` component may carry template-only properties `repeat: "$data.<alias>.<listField>"`, `limit` (1..20, default 10) and `empty` (text shown when the list is empty). Its children are cloned per item; inside them `$item.<key>` is replaced with the item's value. Template-only properties never reach the client.
- Actions: `actionId` must be `widget.setRange` or a registered action; a registered action needs `actionValue` = the target (a literal target some source of this widget declares, or `$item.<key>` inside a repeat over a source that declares targets).
- [ ] **Step 1: Write the failing template tests**

```python
# tests/test_widgets_template.py
from __future__ import annotations

import pytest


@pytest.fixture
def sources():
    from eve.widgets.sources import parse

    return {
        "kitchen": parse({"type": "home.entity", "entities": ["light.kitchen"]}),
        "cal": parse({"type": "calendar"}),
    }


def accepts(action_id, target):
    return action_id == "home.toggle" and target.startswith("light.")


def card(*children, **props):
    return {"id": "root", "type": "card", "properties": props, "children": list(children)}


def text(value, id="t"):
    return {"id": id, "type": "text", "properties": {"text": value}, "children": []}


def test_a_valid_template_passes(sources):
    from eve.widgets import template

    components = [card(text("$data.kitchen.primary.state_label"), actionId="home.toggle", actionValue="light.kitchen")]
    assert template.validate(components, sources, action_accepts=accepts) is None


def test_unknown_alias_is_named_in_the_error(sources):
    from eve.widgets import template

    error = template.validate([card(text("$data.garage.primary.state"))], sources, action_accepts=accepts)
    assert "garage" in error and "kitchen" in error


def test_unknown_field_is_rejected(sources):
    from eve.widgets import template

    error = template.validate([card(text("$data.kitchen.nope"))], sources, action_accepts=accepts)
    assert "nope" in error


def test_an_action_on_an_undeclared_target_is_rejected(sources):
    from eve.widgets import template

    components = [card(text("x"), actionId="home.toggle", actionValue="light.bedroom")]
    assert "light.bedroom" in template.validate(components, sources, action_accepts=accepts)


def test_an_unregistered_action_is_rejected(sources):
    from eve.widgets import template

    components = [card(text("x"), actionId="home.explode", actionValue="light.kitchen")]
    assert "home.explode" in template.validate(components, sources, action_accepts=accepts)


def test_item_outside_a_repeat_is_rejected(sources):
    from eve.widgets import template

    assert "$item" in template.validate([card(text("$item.summary"))], sources, action_accepts=accepts)


def test_repeat_expands_items_and_strips_template_properties():
    from eve.widgets import template

    components = [{"id": "events", "type": "list",
                   "properties": {"repeat": "$data.cal.items", "limit": 2, "empty": "Nothing"},
                   "children": [text("$item.summary", id="s")]}]
    data = {"cal": {"items": [{"summary": "A"}, {"summary": "B"}, {"summary": "C"}]}}

    rendered, problems = template.render(components, data)

    assert rendered[0]["properties"] == {}
    assert [c["properties"]["text"] for c in rendered[0]["children"]] == ["A", "B"]
    assert [c["id"] for c in rendered[0]["children"]] == ["s_0", "s_1"]
    assert problems == []


def test_repeat_over_nothing_shows_the_empty_text():
    from eve.widgets import template

    components = [{"id": "events", "type": "list", "properties": {"repeat": "$data.cal.items", "empty": "Nothing"},
                   "children": [text("$item.summary")]}]
    rendered, _ = template.render(components, {"cal": {"items": []}})
    assert rendered[0]["children"][0]["properties"]["text"] == "Nothing"


def test_an_unresolvable_binding_degrades_to_a_dash_not_a_broken_widget():
    """A binding the client cannot resolve makes the WHOLE surface fall back
    to "This content can't be shown". One missing value must not do that."""
    from eve.widgets import template

    rendered, problems = template.render([card(text("$data.kitchen.primary.state"))], {"kitchen": {}})
    assert rendered[0]["children"][0]["properties"]["text"] == "—"
    assert problems == ["binding"]


def test_a_template_that_fails_the_catalog_is_rejected(sources):
    from eve.widgets import template

    bad = [{"id": "x", "type": "marquee", "properties": {}, "children": []}]
    assert "component-type" in template.validate(bad, sources, action_accepts=accepts)
```

Run → FAIL.

- [ ] **Step 2: Implement** `template.py`

```python
# src/eve/widgets/template.py
"""Model-authored widget templates: checked once at save, rendered on every
refresh with no model.

A template is an `assistant-ui/1.0` component tree plus two template-only
affordances the client never sees (`repeat` on a list, and `$item.` inside
it). Bindings stay the client's own `$data.` syntax, so rendering is mostly
"hand the client the tree and the data"; the server only expands repeats and
patches bindings that did not resolve.
"""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable

from pydantic import BaseModel

from eve.ui import protocol
from eve.widgets.sources.base import SourceType

WIDGET_ALIAS = "widget"
WIDGET_FIELDS = frozenset({"title", "days", "empty_points"})
MAX_TEMPLATE_BYTES = 12_288
MAX_REPEAT = 20
DEFAULT_REPEAT = 10
MISSING = "—"

_DATA = re.compile(r"^\$data((?:\.[A-Za-z_][A-Za-z0-9_]*)+)$")
_ITEM = re.compile(r"^\$item((?:\.[A-Za-z_][A-Za-z0-9_]*)+)$")
_TEMPLATE_ONLY = ("repeat", "limit", "empty")

Sources = dict[str, tuple[SourceType, BaseModel]]


def _strings(properties: dict):
    for key, value in properties.items():
        if isinstance(value, str):
            yield key, value
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, str):
                    yield key, entry


def validate(components: object, sources: Sources, *, action_accepts: Callable[[str, str], bool]) -> str | None:
    """None, or a message that tells the model exactly what to change."""
    if not isinstance(components, list) or not components:
        return "template must be a non-empty list of components"
    if len(json.dumps(components).encode()) > MAX_TEMPLATE_BYTES:
        return f"template is larger than {MAX_TEMPLATE_BYTES} bytes; simplify it"

    declared = ", ".join(sorted(sources)) or "none"
    all_targets = frozenset().union(*(s.targets(p) for s, p in sources.values())) if sources else frozenset()

    def check_data(path: str) -> str | None:
        alias, _, rest = path.lstrip(".").partition(".")
        field = rest.split(".", 1)[0]
        if alias == WIDGET_ALIAS:
            return None if field in WIDGET_FIELDS else f"$data.widget.{field} does not exist ({sorted(WIDGET_FIELDS)})"
        if alias not in sources:
            return f"binding $data{path} names source {alias!r}, but the declared sources are: {declared}"
        source, _ = sources[alias]
        if field not in source.fields:
            return f"source {alias!r} ({source.name}) has no field {field!r}; fields: {sorted(source.fields)}"
        return None

    def visit(node: object, repeat: tuple[SourceType, BaseModel, str] | None) -> str | None:
        if not isinstance(node, dict):
            return "every component must be an object"
        properties = node.get("properties") or {}
        if not isinstance(properties, dict):
            return f"component {node.get('id')!r}: properties must be an object"

        child_repeat = repeat
        if "repeat" in properties:
            if node.get("type") != "list":
                return f"component {node.get('id')!r}: only a list may repeat"
            match = _DATA.match(str(properties["repeat"]))
            alias, _, field = (match.group(1).lstrip(".") if match else "").partition(".")
            if not match or alias not in sources or field not in sources[alias][0].item_fields:
                lists = {a: sorted(s.item_fields) for a, (s, _) in sources.items() if s.item_fields}
                return f"repeat must be $data.<alias>.<list field>; repeatable fields: {lists}"
            limit = properties.get("limit", DEFAULT_REPEAT)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_REPEAT:
                return f"limit must be an integer 1-{MAX_REPEAT}"
            if "empty" in properties and not isinstance(properties["empty"], str):
                return "empty must be a string"
            child_repeat = (*sources[alias], field)

        for key, value in _strings(properties):
            if key in _TEMPLATE_ONLY:
                continue
            if value.startswith("$data"):
                if not _DATA.match(value):
                    return f"malformed binding {value!r}"
                error = check_data(_DATA.match(value).group(1))
                if error:
                    return error
            elif value.startswith("$item"):
                if repeat is None:
                    return f"{value} is only legal inside a list with repeat"
                keys = repeat[0].item_fields.get(repeat[2]) or frozenset()
                item_key = value.split(".", 2)[1] if _ITEM.match(value) else ""
                if keys and item_key not in keys:
                    return f"{value}: items of {repeat[2]} have keys {sorted(keys)}"

        action = properties.get("actionId")
        if isinstance(action, str) and action not in protocol.ACTION_IDS and action != "widget.setRange":
            target = properties.get("actionValue")
            if isinstance(target, str) and target.startswith("$item"):
                if repeat is None or not repeat[0].targets(repeat[1]):
                    return f"{action}: an $item target needs a repeat over a source with targets"
            elif not isinstance(target, str) or target not in all_targets:
                return (f"{action} targets {target!r}, which no source of this widget declares; "
                        f"declared targets: {sorted(all_targets)}")
            elif not action_accepts(action, target):
                return f"action {action!r} does not exist or cannot act on {target!r}"

        for child in node.get("children") or []:
            error = visit(child, child_repeat)
            if error:
                return error
        return None

    for component in components:
        error = visit(component, None)
        if error:
            return error

    probe = _probe(components)
    error = protocol.validate_operation({"protocol": protocol.PROTOCOL, "op": "create", "surface": {
        "surfaceId": "widget:probe", "catalogId": "column", "catalogVersion": protocol.CATALOG_VERSION,
        "components": probe, "data": {}, "localState": {}}}, widget=True)
    return f"template failed catalog validation: {error}" if error else None


def _probe(components: list) -> list:
    """The tree with template-only syntax replaced by legal stand-ins, so the
    catalog validator judges everything else exactly as the client will."""
    def fix(node):
        node = dict(node)
        properties = {k: v for k, v in (node.get("properties") or {}).items() if k not in _TEMPLATE_ONLY}
        node["properties"] = {
            k: ("item" if isinstance(v, str) and v.startswith("$item") else v) for k, v in properties.items()
        }
        node["children"] = [fix(child) for child in node.get("children") or []]
        return node
    return [fix(component) for component in components]


def _lookup(data: dict, path: str) -> tuple[bool, object]:
    cursor: object = data
    for segment in path.lstrip(".").split("."):
        if isinstance(cursor, dict) and segment in cursor:
            cursor = cursor[segment]
        else:
            return False, None
    return True, cursor


def _display(value: object) -> str:
    if value is None:
        return MISSING
    if isinstance(value, bool):
        return "On" if value else "Off"
    return str(value)


def render(components: list, data: dict) -> tuple[list, list[str]]:
    """The tree the client receives, and any degradations (`binding`)."""
    problems: list[str] = []

    def substitute_item(node: dict, item: dict, suffix: str) -> dict:
        node = copy.deepcopy(node)
        node["id"] = f"{node['id']}{suffix}"
        properties = node.get("properties") or {}
        for key, value in list(properties.items()):
            if isinstance(value, str) and _ITEM.match(value):
                found, resolved = _lookup(item, _ITEM.match(value).group(1))
                if key == "actionValue" and found and isinstance(resolved, (str, int, float, bool)):
                    properties[key] = resolved
                else:
                    properties[key] = _display(resolved) if found else MISSING
        node["children"] = [substitute_item(child, item, suffix) for child in node.get("children") or []]
        return node

    def expand(node: dict) -> dict:
        node = copy.deepcopy(node)
        properties = node.get("properties") or {}
        repeat = properties.pop("repeat", None)
        limit = properties.pop("limit", DEFAULT_REPEAT)
        empty = properties.pop("empty", None)
        if repeat is not None:
            _, items = _lookup(data, _DATA.match(repeat).group(1))
            items = [item for item in items if isinstance(item, dict)][:limit] if isinstance(items, list) else []
            template_children = node.get("children") or []
            children = [
                substitute_item(child, item, f"_{index}")
                for index, item in enumerate(items)
                for child in template_children
            ]
            if not children and empty:
                children = [{"id": f"{node['id']}_empty", "type": "text", "properties": {"text": empty}, "children": []}]
            node["children"] = children
        node["children"] = [expand(child) for child in node.get("children") or []]
        for key, value in list(properties.items()):
            if isinstance(value, str) and _DATA.match(value):
                found, _ = _lookup(data, _DATA.match(value).group(1))
                if not found:
                    problems.append("binding")
                    properties[key] = "$data.widget.empty_points" if key == "points" else MISSING
        node["properties"] = properties
        return node

    return [expand(component) for component in components], problems
```

- [ ] **Step 3: Write the failing preset + recipe tests**

```python
# tests/test_widgets_presets.py
from __future__ import annotations

import pytest

from tests.test_widgets_template import accepts  # the same fake action check


@pytest.mark.parametrize("name,options", [
    ("weather", {"days": 3}),
    ("entity", {"entity": "light.kitchen"}),
    ("entity", {"entity": "sensor.outside"}),
    ("glance", {"entities": ["light.kitchen", "sensor.outside"]}),
    ("media", {"entity": "media_player.living_room"}),
    ("chart", {"sources": [{"type": "records", "collection": "alpha"}], "metric": {"op": "count"}}),
])
def test_every_preset_builds_a_valid_recipe(name, options):
    from eve.widgets import presets, recipe

    built = presets.build(name, options)
    assert isinstance(built, dict), built
    assert recipe.validate(built, action_accepts=lambda a, t: True) is None


def test_entity_preset_makes_the_card_the_tap_target_only_when_toggleable():
    from eve.widgets import presets

    light = presets.build("entity", {"entity": "light.kitchen"})
    sensor = presets.build("entity", {"entity": "sensor.outside"})
    assert light["template"][0]["properties"]["actionId"] == "home.toggle"
    assert "actionId" not in sensor["template"][0]["properties"]


def test_unknown_preset_lists_the_real_ones():
    from eve.widgets import presets

    assert "weather" in presets.build("nope", {})


def test_bad_options_are_explained():
    from eve.widgets import presets

    assert "days" in presets.build("weather", {"days": 99})
```

```python
# tests/test_widgets_recipe.py  (replace the file's v2-relevant tests with these)
from __future__ import annotations

V2 = {
    "version": 2,
    "sources": {"kitchen": {"type": "home.entity", "entities": ["light.kitchen"]}},
    "template": [{"id": "root", "type": "card", "properties": {"title": "$data.kitchen.primary.name"}, "children": []}],
}


def ok(action, target):
    return True


def test_a_v2_recipe_validates():
    from eve.widgets import recipe

    assert recipe.validate(V2, action_accepts=ok) is None


def test_top_level_smuggling_is_rejected():
    from eve.widgets import recipe

    assert recipe.validate({**V2, "url": "http://x"}, action_accepts=ok) is not None


def test_reserved_and_malformed_aliases_are_rejected():
    from eve.widgets import recipe

    for alias in ("widget", "Bad-Alias", "1x"):
        bad = {**V2, "sources": {alias: V2["sources"]["kitchen"]}}
        assert recipe.validate(bad, action_accepts=ok) is not None


def test_too_many_sources_is_rejected():
    from eve.widgets import recipe

    many = {f"s{i}": {"type": "weather"} for i in range(5)}
    assert "4" in recipe.validate({**V2, "sources": many}, action_accepts=ok)


def test_v1_recipes_upgrade_to_the_chart_preset():
    from eve.widgets import recipe

    v1 = {"sources": [{"type": "records", "collection": "alpha"}], "metric": {"op": "count"}}
    upgraded = recipe.upgrade(v1)
    assert upgraded["version"] == 2
    assert upgraded["sources"]["series"]["type"] == "series"


def test_permissions_targets_and_ttl_come_from_the_sources():
    from eve.widgets import recipe

    both = {**V2, "sources": {**V2["sources"], "sky": {"type": "weather"}}}
    assert recipe.required_permissions(both) == ["home.control"]
    assert recipe.declared_targets(both) == {"light.kitchen"}
    assert recipe.ttl_seconds(both) == 10


def test_filters_rules_are_unchanged():
    from eve.widgets import recipe

    assert recipe.validate_filters({"days": 7}) is None
    assert recipe.validate_filters({"days": 0}) == "filters"
```

Run: `uv run pytest tests/test_widgets_presets.py tests/test_widgets_recipe.py -q` → FAIL.

- [ ] **Step 4: Rewrite** `recipe.py`

```python
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
```

Replace the `...` in `validate_filters` with the existing function body, copied verbatim (it references `MAX_DAYS`, `SOURCE_TYPES`→`V1_SOURCE_TYPES`, `MAX_NAME`, `FILTER_KEYS`). Also export `MAX_DAYS` from `sources/series.py` (it was a recipe constant).

- [ ] **Step 5: Implement** `presets.py`

```python
# src/eve/widgets/presets.py
"""Built-in templates. A preset is just a recipe builder the model can pick
instead of authoring a template: consistent layouts for common widgets, and
the reference examples a custom template can copy from.

Adding a preset = one options model + one builder + one `_register` call.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from eve.widgets.sources.home import EntityParams, MediaParams

# Domains where the whole card toggles. Kept in step with
# `eve.widgets.actions.home.TOGGLE_RISK` by a test (Task A7).
TOGGLE_DOMAINS = frozenset({"light", "switch", "fan", "input_boolean", "lock", "cover"})


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    options: type[BaseModel]
    build: Callable[[BaseModel], dict]


PRESETS: dict[str, Preset] = {}


def _register(preset: Preset) -> None:
    PRESETS[preset.name] = preset


def _c(id: str, type: str, properties: dict | None = None, *children: dict) -> dict:
    return {"id": id, "type": type, "properties": properties or {}, "children": list(children)}


def _recipe(sources: dict, *template: dict) -> dict:
    return {"version": 2, "sources": sources, "template": list(template)}


class WeatherOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(default=5, ge=1, le=7)


def _weather(o: WeatherOptions) -> dict:
    return _recipe(
        {"weather": {"type": "weather", "days": o.days}},
        _c("root", "column", {},
           _c("now", "row", {},
              _c("now_icon", "icon", {"name": "$data.weather.current.icon"}),
              _c("now_temp", "text", {"text": "$data.weather.current.temperature"}),
              _c("now_cond", "text", {"text": "$data.weather.current.condition"})),
           _c("feels", "text", {"text": "$data.weather.current.feels_like"}),
           _c("days", "list", {"repeat": "$data.weather.days", "limit": o.days},
              _c("day", "row", {},
                 _c("day_name", "text", {"text": "$item.day"}),
                 _c("day_icon", "icon", {"name": "$item.icon"}),
                 _c("day_range", "text", {"text": "$item.range"})))),
    )


class EntityOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str

    def params(self) -> EntityParams:
        return EntityParams(entities=[self.entity])


def _entity(o: EntityOptions) -> dict:
    o.params()  # validates the id
    toggleable = o.entity.split(".", 1)[0] in TOGGLE_DOMAINS
    card_props = {"actionId": "home.toggle", "actionValue": o.entity} if toggleable else {}
    return _recipe(
        {"entity": {"type": "home.entity", "entities": [o.entity]}},
        _c("root", "card", card_props,
           _c("row", "row", {},
              _c("icon", "icon", {"name": "$data.entity.primary.icon"}),
              _c("name", "text", {"text": "$data.entity.primary.name"}),
              _c("state", "badge", {"label": "$data.entity.primary.value"}))),
    )


class GlanceOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: list[str] = Field(min_length=1, max_length=12)


def _glance(o: GlanceOptions) -> dict:
    EntityParams(entities=o.entities)
    return _recipe(
        {"home": {"type": "home.entity", "entities": o.entities}},
        _c("root", "list", {"repeat": "$data.home.items", "limit": len(o.entities)},
           _c("row", "row", {},
              _c("icon", "icon", {"name": "$item.icon"}),
              _c("name", "text", {"text": "$item.name"}),
              _c("value", "badge", {"label": "$item.value"}))),
    )


class MediaOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str


def _media(o: MediaOptions) -> dict:
    MediaParams(entity=o.entity)
    e = o.entity
    return _recipe(
        {"media": {"type": "home.media", "entity": e}},
        _c("root", "column", {},
           _c("title", "text", {"text": "$data.media.player.title"}),
           _c("artist", "text", {"text": "$data.media.player.artist"}),
           _c("transport", "row", {},
              _c("prev", "button", {"label": "Previous", "actionId": "home.media.previous", "actionValue": e}),
              _c("play", "button", {"label": "$data.media.player.play_label", "actionId": "home.media.play_pause", "actionValue": e}),
              _c("next", "button", {"label": "Next", "actionId": "home.media.next", "actionValue": e})),
           _c("volume", "row", {},
              _c("down", "button", {"label": "Quieter", "actionId": "home.media.volume_down", "actionValue": e}),
              _c("level", "text", {"text": "$data.media.player.volume"}),
              _c("up", "button", {"label": "Louder", "actionId": "home.media.volume_up", "actionValue": e}))),
    )


class ChartOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[dict]
    metric: dict


def _chart(o: ChartOptions) -> dict:
    return _recipe(
        {"series": {"type": "series", "sources": o.sources, "metric": o.metric}},
        _c("root", "column", {},
           _c("range", "segmentedSelection",
              {"options": ["7", "30", "90"], "selected": "$data.widget.days", "actionId": "widget.setRange"}),
           _c("chart", "chart", {"points": "$data.series.points"}),
           _c("note", "text", {"text": "$data.series.note"})),
    )


_register(Preset("weather", "Current conditions and a daily forecast. Options: days (1-7).", WeatherOptions, _weather))
_register(Preset("entity", "One Home Assistant entity; tapping the card toggles lights, switches, fans, locks and "
                 "covers (locks and covers ask first). Options: entity.", EntityOptions, _entity))
_register(Preset("glance", "Read-only state of several entities. Options: entities (1-12).", GlanceOptions, _glance))
_register(Preset("media", "Now playing plus transport and volume for a media player. Options: entity "
                 "(media_player.*).", MediaOptions, _media))
_register(Preset("chart", "A day-bucketed chart of recorded collections and/or health metrics with a 7/30/90 day "
                 "range. Options: sources, metric (see the series source).", ChartOptions, _chart))


def build(name: str, options: dict | None) -> dict | str:
    preset = PRESETS.get(name)
    if preset is None:
        return f"unknown preset {name!r}; presets: {sorted(PRESETS)}"
    try:
        return preset.build(preset.options.model_validate(options or {}))
    except (ValidationError, ValueError) as exc:
        return f"preset {name!r} options rejected: {exc}"
```

(`series.py` must accept the v1 chart shape through `SeriesParams`; the `chart` preset's recipe validates via `recipe.validate` in the test.)

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_widgets_template.py tests/test_widgets_presets.py tests/test_widgets_recipe.py tests/test_widgets_source_series.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/eve/widgets/template.py src/eve/widgets/presets.py src/eve/widgets/recipe.py src/eve/widgets/sources/series.py \
  tests/test_widgets_template.py tests/test_widgets_presets.py tests/test_widgets_recipe.py tests/test_widgets_source_series.py
git commit -m "feat(widgets): v2 recipes with model-authored templates and presets (ENG-269)"
```

---

### Task A7: Action registry, registry-driven snapshots, and the resource routes

**Files:**

* Create: `src/eve/widgets/actions/__init__.py`, `actions/base.py`, `actions/filters.py`, `actions/home.py`
* Modify: `src/eve/widgets/resolve.py` (rewrite), `src/eve/widgets/app.py` (capabilities + `run_action`)
* Test: `tests/test_widgets_actions.py`, `tests/test_widgets_resolve.py` (rewrite), `tests/test_widgets_app.py` (extend)

**Interfaces:**

- Consumes: A1 to A6.
- Produces:
  * `actions.base.Risk = Literal["safe", "confirm"]`; `ActionContext(member: dict, resource: dict, target: str | None, input: dict, expected_revision: int)`; `ActionType(name, label, run, risk_for: Callable[[str | None], Risk | None], default_risk: Risk = "confirm", permission: str | None = None, targeted: bool = True)`; `ActionRejected`, `ActionConflict`, `ActionFailed`; `REGISTRY`, `register`, `accepts(action_id, target) -> bool`.
  * Registered: `filters.replace` (safe, `targeted=False`, returns the updated row); `home.toggle` (safe for light/switch/fan/input_boolean, confirm for lock/cover); `home.media.play_pause|next|previous|volume_up|volume_down` (safe, media_player.\* only). Home actions need `home.control`.
  * `resolve.snapshot(resource: dict, member_sub: str, *, registry: dict | None = None) -> dict` with `refreshAfterSeconds` and `actionRisk`.
  * `app.py` action route semantics as in "Wire contract".
- [ ] **Step 1: Write the failing action tests**

```python
# tests/test_widgets_actions.py
from __future__ import annotations

import json

import pytest


def test_toggle_risk_depends_on_the_domain():
    from eve.widgets.actions import REGISTRY

    toggle = REGISTRY["home.toggle"]
    assert toggle.risk_for("light.kitchen") == "safe"
    assert toggle.risk_for("lock.front_door") == "confirm"
    assert toggle.risk_for("cover.garage") == "confirm"
    assert toggle.risk_for("sensor.outside") is None  # not toggleable at all


def test_preset_toggle_domains_match_the_action():
    from eve.widgets import presets
    from eve.widgets.actions.home import TOGGLE_RISK

    assert presets.TOGGLE_DOMAINS == set(TOGGLE_RISK)


def test_media_actions_only_accept_media_players():
    from eve.widgets.actions import accepts

    assert accepts("home.media.next", "media_player.lr")
    assert not accepts("home.media.next", "light.kitchen")
    assert not accepts("home.nope", "light.kitchen")


@pytest.fixture
def calls(monkeypatch):
    from eve.widgets.actions import home

    seen = []

    async def fake_invoke(tool, arguments):
        seen.append((tool, arguments))
        if tool == "home.get_state":
            return json.dumps({"entity_id": arguments["entity_id"], "state": "locked"})
        return json.dumps({"called": True})

    monkeypatch.setattr(home, "invoke", fake_invoke)
    return seen


def _ctx(target):
    from eve.widgets.actions.base import ActionContext

    return ActionContext(member={"sub": "s", "permissions": ["home.control"]}, resource={}, target=target,
                         input={"target": target}, expected_revision=1)


async def test_toggling_a_light_calls_its_domain_toggle(calls):
    from eve.widgets.actions import REGISTRY

    await REGISTRY["home.toggle"].run(_ctx("light.kitchen"))
    assert calls == [("home.call_service", {"domain": "light", "service": "toggle", "entity_id": "light.kitchen", "data": {}})]


async def test_toggling_a_lock_reads_state_then_unlocks(calls):
    """HA has no lock.toggle; the action decides from the current state."""
    from eve.widgets.actions import REGISTRY

    await REGISTRY["home.toggle"].run(_ctx("lock.front_door"))
    assert calls[-1][1]["service"] == "unlock"


async def test_an_upstream_failure_is_action_failed(monkeypatch):
    from eve.widgets.actions import REGISTRY, home
    from eve.widgets.actions.base import ActionFailed

    async def down(tool, arguments):
        return "error: eve-tools unavailable (ConnectError)"

    monkeypatch.setattr(home, "invoke", down)
    with pytest.raises(ActionFailed):
        await REGISTRY["home.toggle"].run(_ctx("light.kitchen"))
```

Run → FAIL.

- [ ] **Step 2: Implement the action package**

```python
# src/eve/widgets/actions/base.py
"""Audited widget actions: the only writes a widget can make.

An action is registered in code with a risk class (UX: `confirm` makes the
client ask first), a permission (security) and a target check (security: an
action only ever acts on a target the widget's own sources declared, which
the route enforces before `run` is called).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

Risk = Literal["safe", "confirm"]


class ActionRejected(Exception):
    """400: the request is well-formed but not allowed (bad input/target)."""


class ActionConflict(Exception):
    """409: the resource moved; the route answers with the current snapshot."""


class ActionFailed(Exception):
    """502: the upstream system failed. The message is logged, not returned."""


@dataclass(frozen=True)
class ActionContext:
    member: dict
    resource: dict
    target: str | None
    input: dict
    expected_revision: int


@dataclass(frozen=True)
class ActionType:
    name: str
    label: str  # imperative, sentence case: shown on the client's confirm sheet
    # Returns the updated resource row when the action changes the widget
    # itself (filters), else None.
    run: Callable[[ActionContext], Awaitable[dict | None]]
    # Risk for a given target, or None when the action cannot act on it.
    risk_for: Callable[[str | None], Risk | None]
    default_risk: Risk = "confirm"
    permission: str | None = None
    # False for actions on the widget itself (no target check).
    targeted: bool = True


REGISTRY: dict[str, ActionType] = {}


def register(action: ActionType) -> ActionType:
    if action.name in REGISTRY:
        raise ValueError(f"action {action.name!r} registered twice")
    REGISTRY[action.name] = action
    return action


def accepts(action_id: str, target: str) -> bool:
    action = REGISTRY.get(action_id)
    return action is not None and action.targeted and action.risk_for(target) is not None
```

```python
# src/eve/widgets/actions/filters.py
"""`filters.replace`: persist the widget's whole filter state, revision-guarded."""
from __future__ import annotations

from eve.widgets import recipe as recipe_rules
from eve.widgets import store
from eve.widgets.actions.base import ActionConflict, ActionContext, ActionRejected, ActionType, register


async def _run(ctx: ActionContext) -> dict:
    error = recipe_rules.validate_filters(ctx.input)
    if error is not None:
        raise ActionRejected(f"invalid filters: {error}")
    updated = await store.update_filters(ctx.member["sub"], ctx.resource["id"], ctx.input, ctx.expected_revision)
    if updated is None:
        raise ActionConflict()
    return updated


register(ActionType(name="filters.replace", label="Apply", run=_run, risk_for=lambda _t: "safe",
                    default_risk="safe", targeted=False))
```

```python
# src/eve/widgets/actions/home.py
"""Home Assistant actions. Adding one = one `register` call below."""
from __future__ import annotations

import json

from eve.tools_client import invoke
from eve.widgets.actions.base import ActionContext, ActionFailed, ActionType, Risk, register

# Everyday switches are one tap. Anything that opens the house asks first.
TOGGLE_RISK: dict[str, Risk] = {
    "light": "safe", "switch": "safe", "fan": "safe", "input_boolean": "safe",
    "lock": "confirm", "cover": "confirm",
}


def _domain(target: str | None) -> str:
    return (target or "").split(".", 1)[0]


async def _call(tool: str, arguments: dict) -> dict:
    raw = await invoke(tool, arguments)
    if raw.startswith("error:"):
        raise ActionFailed(f"{tool} failed")
    return json.loads(raw)


async def _service(domain: str, service: str, entity_id: str) -> None:
    await _call("home.call_service", {"domain": domain, "service": service, "entity_id": entity_id, "data": {}})


async def _toggle(ctx: ActionContext) -> None:
    domain = _domain(ctx.target)
    if domain == "lock":
        state = (await _call("home.get_state", {"entity_id": ctx.target})).get("state")
        await _service("lock", "unlock" if state == "locked" else "lock", ctx.target)
    else:
        await _service(domain, "toggle", ctx.target)
    return None


register(ActionType(
    name="home.toggle", label="Toggle", run=_toggle,
    risk_for=lambda target: TOGGLE_RISK.get(_domain(target)),
    default_risk="safe", permission="home.control",
))


def _media(name: str, service: str, label: str) -> None:
    async def run(ctx: ActionContext) -> None:
        await _service("media_player", service, ctx.target)

    register(ActionType(
        name=name, label=label, run=run,
        risk_for=lambda target: "safe" if _domain(target) == "media_player" else None,
        default_risk="safe", permission="home.control",
    ))


_media("home.media.play_pause", "media_play_pause", "Play or pause")
_media("home.media.next", "media_next_track", "Next track")
_media("home.media.previous", "media_previous_track", "Previous track")
_media("home.media.volume_up", "volume_up", "Louder")
_media("home.media.volume_down", "volume_down", "Quieter")
```

```python
# src/eve/widgets/actions/__init__.py
"""Importing this package registers every action."""
from eve.widgets.actions.base import REGISTRY, accepts  # noqa: F401
from eve.widgets.actions import filters, home  # noqa: E402,F401
```

Run: `uv run pytest tests/test_widgets_actions.py -q` → PASS.

- [ ] **Step 3: Rewrite the resolver tests (failing)**

Replace `tests/test_widgets_resolve.py` with tests against the registry. Use a fake registry passed via `registry=`:

```python
# tests/test_widgets_resolve.py
"""A refresh is sources + template, no model. Pure over an injected registry,
so every partial-failure branch is a unit test."""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from eve.ui import protocol


class P(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _source(name, read, ttl=60, targets=frozenset()):
    from eve.widgets.sources.base import SourceType

    return SourceType(name=name, params=P, fields=frozenset({"v", "items"}), read=read, ttl_seconds=ttl,
                      item_fields={"items": frozenset()}, targets=lambda p: targets)


async def good(ctx, params):
    return {"v": "hello", "items": [{"n": "a"}]}


async def bad(ctx, params):
    raise RuntimeError("upstream down, token=secret")


def _resource(sources, template):
    return {"id": "res-1", "kind": "custom", "title": "T", "revision": 3, "filters": {},
            "recipe": {"version": 2, "sources": sources, "template": template}}


TEMPLATE = [{"id": "root", "type": "card", "properties": {}, "children": [
    {"id": "t", "type": "text", "properties": {"text": "$data.a.v"}, "children": []}]}]


@pytest.fixture
def registry(monkeypatch):
    from eve.widgets import sources

    fake = {"good": _source("good", good, ttl=30, targets=frozenset({"light.kitchen"})), "bad": _source("bad", bad, ttl=10)}
    monkeypatch.setattr(sources.base, "REGISTRY", fake)
    return fake


async def test_snapshot_fills_data_and_is_a_valid_widget_surface(registry):
    from eve.widgets import resolve

    snap = await resolve.snapshot(_resource({"a": {"type": "good"}}, TEMPLATE), "sub-noah")

    assert snap["view"]["data"]["a"]["v"] == "hello"
    assert snap["view"]["data"]["widget"]["title"] == "T"
    assert snap["refreshAfterSeconds"] == 30
    op = {"protocol": protocol.PROTOCOL, "op": "create", "surface": {"surfaceId": "w", "catalogId": "column",
          "catalogVersion": "1", "components": snap["view"]["components"], "data": snap["view"]["data"], "localState": {}}}
    assert protocol.validate_operation(op, widget=True) is None


async def test_a_failed_source_is_partial_and_never_leaks_its_message(registry):
    from eve.widgets import resolve

    snap = await resolve.snapshot(_resource({"a": {"type": "good"}, "b": {"type": "bad"}}, TEMPLATE), "s")

    assert snap["sources"] == {"partial": True, "errors": [{"source": "b", "reason": "unavailable"}]}
    assert "secret" not in str(snap)
    assert snap["refreshAfterSeconds"] == 10


async def test_risk_overrides_are_published_per_target(registry, monkeypatch):
    from eve.widgets import resolve
    from eve.widgets.actions import REGISTRY

    snap = await resolve.snapshot(_resource({"a": {"type": "good"}}, TEMPLATE), "s")
    # light.kitchen is safe for home.toggle == its default, so no override;
    # overrides appear only where risk differs from the action's default.
    assert "home.toggle:light.kitchen" not in snap["actionRisk"]


async def test_a_v1_recipe_still_renders_as_a_chart(monkeypatch):
    from eve.widgets import resolve
    from eve.widgets.sources import series

    async def rows(member_sub, collection, since=None, until=None, limit=500):
        return []

    monkeypatch.setattr(series.record_store, "query", rows)
    resource = {"id": "r", "kind": "chart", "title": "Old", "revision": 1, "filters": {"days": 7},
                "recipe": {"sources": [{"type": "records", "collection": "alpha"}], "metric": {"op": "count"}}}
    snap = await resolve.snapshot(resource, "s")
    assert snap["view"]["data"]["widget"]["days"] == "7"
    assert any(c["type"] == "segmentedSelection" for c in snap["view"]["components"][0]["children"])
```

Run → FAIL.

- [ ] **Step 4: Rewrite** `resolve.py`

```python
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
```

Remove the `registry=`, `read_records=`, `read_health=` keyword parameters everywhere (the tests patch `sources.base.REGISTRY` instead). Update `tests/test_widgets_integration.py` to match (it may call `resolve.snapshot` with the old keyword readers: replace those with the registry monkeypatch pattern above).

Note: `recipe.parse_sources` must read the registry through `sources.base.REGISTRY` at call time (`source_registry.parse` does), so the monkeypatch in the test takes effect.

- [ ] **Step 5: Extend the app route tests (failing)**

Append to `tests/test_widgets_app.py`:

```python
ENTITY_RESOURCE = {
    "id": "res-2", "kind": "entity", "title": "Kitchen", "revision": 2, "filters": {},
    "recipe": {"version": 2, "sources": {"entity": {"type": "home.entity", "entities": ["light.kitchen"]}},
               "template": [{"id": "root", "type": "card", "properties": {"actionId": "home.toggle",
                             "actionValue": "light.kitchen"}, "children": []}]},
}


@pytest.fixture
def home_client(monkeypatch):
    from eve.widgets import app as widgets_app

    widgets_app.app.dependency_overrides[widgets_app.require_auth] = lambda: None
    widgets_app.app.dependency_overrides[widgets_app.current_member] = (
        lambda: {"sub": "sub-noah", "permissions": ["home.control"]}
    )

    async def fake_get(member_sub, resource_id):
        return ENTITY_RESOURCE if resource_id == "res-2" else None

    async def fake_snapshot(resource, member_sub):
        return {"resourceId": resource["id"], "revision": resource["revision"]}

    monkeypatch.setattr(widgets_app.store, "get", fake_get)
    monkeypatch.setattr(widgets_app.resolve, "snapshot", fake_snapshot)
    yield TestClient(widgets_app.app)
    widgets_app.app.dependency_overrides.clear()


def _post(client, type, target, revision=2):
    return client.post("/provider-resources/v1/resources/res-2/actions",
                       json={"type": type, "input": {"target": target}, "expectedRevision": revision})


def test_capabilities_advertise_actions_with_risk_and_label(client):
    body = client.get("/provider-resources/v1/capabilities").json()
    toggle = next(a for a in body["actions"] if a["type"] == "home.toggle")
    assert toggle == {"type": "home.toggle", "risk": "safe", "label": "Toggle"}
    assert "weather" in body["sourceTypes"] and "entity" in body["kinds"]


def test_a_toggle_runs_and_returns_the_fresh_snapshot(home_client, monkeypatch):
    from dataclasses import replace

    from eve.widgets.actions import REGISTRY

    ran = []

    async def fake_run(ctx):
        ran.append(ctx.target)

    monkeypatch.setitem(REGISTRY, "home.toggle", replace(REGISTRY["home.toggle"], run=fake_run))

    response = _post(home_client, "home.toggle", "light.kitchen")

    assert response.status_code == 200
    assert ran == ["light.kitchen"]


def test_a_target_the_widget_never_declared_is_400(home_client):
    """A tampered client cannot turn a light widget into a door opener."""
    assert _post(home_client, "home.toggle", "lock.front_door").status_code == 400


def test_an_unknown_action_is_400(home_client):
    assert _post(home_client, "home.explode", "light.kitchen").status_code == 400


def test_an_action_needs_its_permission(home_client):
    from eve.widgets import app as widgets_app

    widgets_app.app.dependency_overrides[widgets_app.current_member] = lambda: {"sub": "sub-noah", "permissions": []}
    assert _post(home_client, "home.toggle", "light.kitchen").status_code == 403


def test_an_upstream_failure_is_502(home_client, monkeypatch):
    from eve.widgets.actions import REGISTRY
    from eve.widgets.actions.base import ActionFailed

    from dataclasses import replace

    async def boom(ctx):
        raise ActionFailed("ha down")

    monkeypatch.setitem(REGISTRY, "home.toggle", replace(REGISTRY["home.toggle"], run=boom))
    response = _post(home_client, "home.toggle", "light.kitchen")
    assert response.status_code == 502
    assert "ha down" not in response.text
```

Run: `uv run pytest tests/test_widgets_app.py -q` → FAIL.

- [ ] **Step 6: Rewrite the routes in** `app.py`

Replace `ACTION_RISK` and the `capabilities` / `run_action` handlers:

```python
from eve.widgets import actions as action_registry
from eve.widgets import presets
from eve.widgets import sources as source_registry
from eve.widgets.actions.base import ActionConflict, ActionContext, ActionFailed, ActionRejected

MIN_REFRESH_SECONDS = 5


@router.get(f"{PREFIX}/capabilities")
async def capabilities(member: dict = Depends(current_member)) -> dict:
    """Generated from the registries: registering a source, action or preset
    is the whole job of advertising it."""
    return {
        "protocol": PROTOCOL,
        "kinds": sorted([*presets.PRESETS, "custom"]),
        "sourceTypes": sorted(source_registry.REGISTRY),
        "actions": [
            {"type": a.name, "risk": a.default_risk, "label": a.label}
            for a in sorted(action_registry.REGISTRY.values(), key=lambda a: a.name)
        ],
        "limits": {"maxDays": recipe_rules.MAX_DAYS, "minRefreshSeconds": MIN_REFRESH_SECONDS},
    }


@router.post(f"{PREFIX}/resources/{{resource_id}}/actions")
async def run_action(resource_id: str, body: ActionRequest, member: dict = Depends(current_member)) -> dict:
    resource = await _load(member, resource_id)
    action = action_registry.REGISTRY.get(body.type)
    if action is None:
        raise HTTPException(status_code=400, detail="unknown action")

    _require_permissions(member, resource)
    if action.permission and permission_denial(member["permissions"], action.permission):
        raise HTTPException(status_code=403, detail="forbidden")

    target = None
    if action.targeted:
        target = body.input.get("target")
        # The security boundary: only targets THIS widget's sources declared.
        if not isinstance(target, str) or target not in recipe_rules.declared_targets(resource["recipe"]):
            raise HTTPException(status_code=400, detail="unknown target")
        if action.risk_for(target) is None:
            raise HTTPException(status_code=400, detail="action cannot act on that target")

    ctx = ActionContext(member=member, resource=resource, target=target, input=body.input,
                        expected_revision=body.expectedRevision)
    try:
        updated = await action.run(ctx)
    except ActionConflict:
        current = await _load(member, resource_id)
        raise HTTPException(status_code=409, detail=await resolve.snapshot(current, member["sub"]))
    except ActionRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ActionFailed:
        logger.warning("widget action %s failed on %s", action.name, resource_id, exc_info=True)
        raise HTTPException(status_code=502, detail="the device did not respond")

    return await resolve.snapshot(updated or resource, member["sub"])
```

Remove the old `ACTION_RISK` map and the inline `filters.replace` logic (now in `actions/filters.py`). Keep `RANGE_ACTION_ID`.

- [ ] **Step 7: Run the whole widget suite**

Run: `uv run pytest tests/test_widgets_*.py tests/test_ui_protocol*.py tests/test_eve_tools_weather.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/eve/widgets tests/test_widgets_actions.py tests/test_widgets_resolve.py tests/test_widgets_app.py tests/test_widgets_integration.py
git commit -m "feat(widgets): action registry, Home Assistant actions, registry-driven snapshots (ENG-269)"
```

---

### Task A8: `save_widget` v2, the authoring skill, docs

**Files:**

* Modify: `src/eve/widgets/tools.py`, `docs/architecture.md` (the `widgets/` block near line 189 and the paragraph near line 245)
* Create: `skills/build-a-widget/SKILL.md`
* Test: `tests/test_widgets_tools.py` (rewrite relevant tests)

**Interfaces:**

- Consumes: `presets.build`, `recipe.validate`, `recipe.required_permissions`, `actions.accepts`, registries for the description.
- Produces: `save_widget(title: str, state, config, preset: str | None = None, options: dict | None = None, sources: dict | None = None, template: list | None = None, filters: dict | None = None) -> str`. Stored `kind` = preset name or `"custom"`.
- [ ] **Step 1: Write the failing tool tests**

Keep the file's `_full_state` helper and the ambient test; replace the rest with:

```python
CONFIG = {"configurable": {"member": {"sub": "sub-noah", "permissions": ["home.control"]}}}
NO_PERMS = {"configurable": {"member": {"sub": "sub-noah", "permissions": []}}}


@pytest.fixture
def stored(monkeypatch):
    from eve.widgets import tools

    saved = {}

    async def fake_create(member_sub, kind, title, recipe, filters):
        saved.update(kind=kind, title=title, recipe=recipe)
        return {"id": "res-9"}

    monkeypatch.setattr(tools.store, "create", fake_create)
    return saved


async def test_a_preset_widget_is_saved_with_its_kind(stored):
    from eve.widgets.tools import save_widget

    out = await save_widget.ainvoke({"title": "Kitchen", "preset": "entity", "options": {"entity": "light.kitchen"},
                                     "state": _full_state([])}, config=CONFIG)
    assert "Saved" in out and stored["kind"] == "entity"


async def test_a_custom_template_is_saved(stored):
    from eve.widgets.tools import save_widget

    out = await save_widget.ainvoke({
        "title": "Today",
        "sources": {"cal": {"type": "calendar", "limit": 3}},
        "template": [{"id": "l", "type": "list", "properties": {"repeat": "$data.cal.items", "limit": 3,
                      "empty": "Nothing today"}, "children": [
                      {"id": "e", "type": "text", "properties": {"text": "$item.summary"}, "children": []}]}],
        "state": _full_state([]),
    }, config=CONFIG)
    assert "Saved" in out and stored["kind"] == "custom"


async def test_a_bad_template_explains_itself(stored):
    from eve.widgets.tools import save_widget

    out = await save_widget.ainvoke({"title": "X", "sources": {"cal": {"type": "calendar"}},
                                     "template": [{"id": "t", "type": "text", "properties": {"text": "$data.nope.x"},
                                                   "children": []}], "state": _full_state([])}, config=CONFIG)
    assert "nope" in out and not stored


async def test_preset_and_template_together_is_rejected(stored):
    from eve.widgets.tools import save_widget

    out = await save_widget.ainvoke({"title": "X", "preset": "weather", "template": [], "state": _full_state([])},
                                    config=CONFIG)
    assert "either" in out.lower()


async def test_permissions_come_from_the_sources(stored):
    from eve.widgets.tools import save_widget

    out = await save_widget.ainvoke({"title": "K", "preset": "entity", "options": {"entity": "light.kitchen"},
                                     "state": _full_state([])}, config=NO_PERMS)
    assert "Permission denied" in out


def test_the_description_lists_every_source_and_preset():
    from eve.widgets import presets, sources
    from eve.widgets.tools import save_widget

    for name in [*sources.REGISTRY, *presets.PRESETS]:
        assert name in save_widget.description
```

Run → FAIL.

- [ ] **Step 2: Rewrite** `tools.py`

```python
# src/eve/widgets/tools.py
"""The one way a widget is created. Everything the model supplies is checked
here; everything it must NOT supply (owner, credentials, endpoints) is
injected or absent by construction."""
from __future__ import annotations

import logging
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from eve.specialists.permissions import permission_denial
from eve.state import EveState, turn_is_ambient
from eve.widgets import actions, presets, recipe as recipe_rules, store
from eve.widgets import sources as source_registry

logger = logging.getLogger(__name__)


def _describe() -> str:
    """Generated from the registries, so a new source/preset/action is
    documented to the model the moment it is registered."""
    preset_lines = "\n".join(f"- {p.name}: {p.description}" for p in presets.PRESETS.values())
    source_lines = "\n".join(f"- {s.name}: {s.description}" for s in source_registry.REGISTRY.values())
    action_lines = "\n".join(
        f"- {a.name} ({a.label})" for a in actions.REGISTRY.values() if a.targeted
    )
    return f"""Save a live widget the member can open from Widgets. It refreshes itself with no model call.

Prefer a preset: pass `preset` and its `options`.
{preset_lines}

For anything else pass `sources` ({{alias: {{type, ...params}}}}, at most 4) and a `template` (a component tree;
see the build-a-widget skill). Bind data as $data.<alias>.<field>; repeat a list with
{{"type": "list", "properties": {{"repeat": "$data.<alias>.<list>", "limit": n, "empty": "..."}}}} and $item.<key>.
Source types:
{source_lines}

Actions (actionId + actionValue = a target one of the widget's sources declares):
{action_lines}

Answer in prose for a one-off question; save a widget when the member wants to keep looking at something."""


@tool(description=_describe())
async def save_widget(
    title: str,
    state: Annotated[EveState, InjectedState],
    config: RunnableConfig,
    preset: str | None = None,
    options: dict | None = None,
    sources: dict | None = None,
    template: list | None = None,
    filters: dict | None = None,
) -> str:
    member = (config.get("configurable") or {}).get("member") or {}
    member_sub = member["sub"]

    if turn_is_ambient(state.get("messages") or []):
        return "A widget cannot be created from an ambient turn."
    if len(title) > recipe_rules.MAX_NAME:
        return f"The widget title is too long: {len(title)} characters, the limit is {recipe_rules.MAX_NAME}."
    if (preset is None) == (template is None):
        return "Pass either a preset (with options) or sources plus a template, not both and not neither."

    if preset is not None:
        built = presets.build(preset, options)
        if isinstance(built, str):
            return f"The widget was rejected: {built}"
        recipe, kind = built, preset
    else:
        recipe, kind = {"version": recipe_rules.RECIPE_VERSION, "sources": sources or {}, "template": template}, "custom"

    error = recipe_rules.validate(recipe, action_accepts=actions.accepts)
    if error is not None:
        return f"The widget was rejected: {error}"

    chosen_filters = filters or {}
    filter_error = recipe_rules.validate_filters(chosen_filters)
    if filter_error is not None:
        return f"The widget filters were rejected: {filter_error}."

    for required in recipe_rules.required_permissions(recipe):
        denial = permission_denial(member.get("permissions", []), required)
        if denial:
            return denial

    try:
        created = await store.create(member_sub, kind, title, recipe, chosen_filters)
    except Exception as exc:
        logger.warning("save_widget failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"
    return f"Saved the widget {title!r} (id {created['id']}). It appears under Widgets and stays up to date on its own."
```

Also update the migration docstring's claim in a comment only if needed; **no schema change** (`recipe` is jsonb, `kind` is text).

- [ ] **Step 3: Write the authoring skill**

```markdown
---
name: build-a-widget
description: How to save a live widget (weather, a light, a speaker, a calendar strip, a health tile) with save_widget - when to use a preset, how to write a template, and what makes a widget good on a phone.
---
A widget is a small card the member keeps on their Widgets screen. It refreshes itself; you are never asked
again. So save one when the member wants to KEEP looking at or controlling something. A one-off question gets
a sentence.

## Prefer a preset

`weather`, `entity`, `glance`, `media` and `chart` cover most requests and look consistent. Use them unless
the member asked for a combination or layout no preset has. Look up entity ids with the home specialist first;
never guess one.

## Writing a template

Declare what you read under `sources` with short aliases, then build the tree from the normal catalog.

- Bind with `$data.<alias>.<field>`; `$data.widget.title` is the widget's own title.
- Show a list with a `list` whose properties are `repeat` (the list to walk), `limit`, and `empty` (what to
  say when there is nothing, e.g. "Nothing scheduled"). Inside it, use `$item.<key>`.
- An action is `actionId` plus `actionValue` = the entity it acts on, and that entity must be one of this
  widget's sources. If a card does ONE obvious thing (toggle this light), put the action on the card itself so
  the whole card is the button, and put no buttons inside it. If there are several controls, use buttons.
- Icons: info, alert, check, sun, moon, cloud, cloud-sun, rain, snow, storm, fog, wind, thermometer, droplets,
  lightbulb, lightbulb-off, power, lock, unlock, fan, plug, home, music, play, pause, skip-forward, skip-back,
  volume-down, volume-up, calendar, heart. Prefer the `icon` field a source gives you over picking one.

## What makes a good widget

- One glance: the headline value first, big; detail second. Five or fewer rows.
- Words over colour: "Locked", not a red dot.
- Button labels are verbs: "Next", "Louder", not "›".
- Title it by what it shows ("Kitchen light"), not by what it is ("Entity widget").
```

- [ ] **Step 4: Update** `docs/architecture.md`

Replace the `widgets/` block with:

```
  widgets/
    sources/        # the audited readers: base.py (registry), series, health, weather, home (entity, media), calendar
    actions/        # the audited writes: base.py (registry, risk, errors), filters, home
    recipe.py       # v2 recipes {version, sources, template}; v1 upgrade; permissions/targets/ttl from sources
    template.py     # template validation (bindings, repeat, action targets) and rendering; no I/O
    presets.py      # built-in templates: weather, entity, glance, media, chart
    icons.py        # icon names a widget may emit (mirrors the client's glyph map)
    resolve.py      # recipe -> snapshot, concurrently, never raises; refreshAfterSeconds + actionRisk
    store.py        # every eve_widget_resource SQL statement
    tools.py        # save_widget: presets or custom templates; description generated from the registries
    app.py          # /provider-resources/v1: capabilities (generated), list, snapshot, actions, delete
```

and replace the dependency paragraph with: "`widgets.sources.*` and `widgets.actions.*` sit on `eve.tools_client` (and `records.store` for `series`); `widgets.template` sits on `eve.ui.protocol` only; `widgets.recipe` on sources and template; `widgets.resolve` on recipe, template and the action registry; `widgets.tools` and `widgets.app` on everything above plus `widgets.store`. Adding a source or action is a new module plus one import in its package `__init__`; nothing else changes."

- [ ] **Step 5: Run the full server suite and lint**

Run: `uv run pytest -q` then `uv run ruff check src tests`
Expected: PASS, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add src/eve/widgets/tools.py skills/build-a-widget docs/architecture.md tests/test_widgets_tools.py
git commit -m "feat(widgets): save_widget v2 with presets and templates, build-a-widget skill (ENG-269)"
```

---

# Part B: Client (`open-assistant`)

Run everything from `~/GitHub/open-assistant/flutter-open-assistant`. Test commands: `flutter test <path>`; for the design package `cd packages/app_ui && flutter test <path>`. Run `flutter analyze` before each commit.

### Task B1: Protocol mirror (widget-mode card action, namespaced ids, no nested interactives)

**Files:**

* Modify: `lib/domain/models/dynamic_ui/dynamic_surface_protocol.dart:232-350`
* Create: `test/fixtures/dynamic_ui/widget_surface_cases.json` (byte copy of `eve-ai/tests/fixtures/widget_surface_cases.json`), `test/domain/models/dynamic_ui/widget_surface_cases_test.dart`

**Interfaces:**

- Produces: `DynamicSurfaceProtocol.validateSurface(surface, widget: true)` accepts the same cases the server does.
- [ ] **Step 1: Copy the case file**

```bash
cp ~/GitHub/eve-ai/tests/fixtures/widget_surface_cases.json test/fixtures/dynamic_ui/widget_surface_cases.json
```

- [ ] **Step 2: Write the failing test**

```dart
// test/domain/models/dynamic_ui/widget_surface_cases_test.dart
import 'dart:convert';
import 'dart:io';

import 'package:assistant/domain/models/dynamic_ui/dynamic_surface.dart';
import 'package:assistant/domain/models/dynamic_ui/dynamic_surface_protocol.dart';
import 'package:flutter_test/flutter_test.dart';

/// Shared byte-for-byte with eve-ai's tests/fixtures/widget_surface_cases.json.
/// The two validators are the one place the repos' contracts must agree.
void main() {
  final cases = (jsonDecode(
    File('test/fixtures/dynamic_ui/widget_surface_cases.json').readAsStringSync(),
  ) as List).cast<Map<String, Object?>>();

  DynamicSurfaceDefinition surface(Object? components) =>
      DynamicSurfaceDefinition.fromJson({
        'surfaceId': 'widget:x',
        'catalogId': 'column',
        'catalogVersion': '1',
        'components': components,
        'data': const <String, Object?>{},
        'localState': const <String, Object?>{},
      });

  for (final c in cases) {
    test('widget mode: ${c['name']}', () {
      final error = DynamicSurfaceProtocol.validateSurface(
        surface(c['components']),
        widget: true,
      );
      expect(error == null, c['valid'], reason: '$error');
    });
  }

  test('chat mode still rejects a tappable card', () {
    expect(
      DynamicSurfaceProtocol.validateSurface(surface(cases[0]['components'])),
      'component-schema',
    );
  });

  test('chat mode still rejects a namespaced action', () {
    expect(
      DynamicSurfaceProtocol.validateSurface(surface(cases[1]['components'])),
      'action-schema',
    );
  });
}
```

Run: `flutter test test/domain/models/dynamic_ui/widget_surface_cases_test.dart` → FAIL on the tappable-card and namespaced cases.

- [ ] **Step 3: Implement**

In `dynamic_surface_protocol.dart`:

1. `_validateComponentProperties`: change the `'card'` arm to

```dart
      'card' => widget ? {'title', 'actionId', 'actionValue'} : {'title'},
```

and after the `button` exactly-one check add:

```dart
    if (widget &&
        component.type == 'card' &&
        component.properties.containsKey('actionId') &&
        _hasInteractiveDescendant(component)) {
      // Two stacked tap targets: the inner one steals the tap on some
      // platforms, and a screen reader announces one control where there
      // are two. Mirrors eve.ui.protocol._has_interactive_descendant.
      return 'action-schema';
    }
```

2. Replace `_validateActionId` and add the helpers:

```dart
  /// Widget surfaces may name any namespaced action; which ones exist is the
  /// provider's call (it rejects unknown actions), so a new server action
  /// needs no client release. Chat surfaces keep [_actionIds] exactly.
  static final _widgetActionId = RegExp(r'^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$');

  static String? _validateActionId(Object? value, {bool widget = false}) =>
      _actionIds.contains(value) ||
              (widget && value is String && _widgetActionId.hasMatch(value))
          ? null
          : 'action-schema';

  static bool _hasInteractiveDescendant(DynamicComponent component) {
    for (final child in component.children) {
      if (child.properties.containsKey('actionId') ||
          child.properties.containsKey('setState') ||
          _hasInteractiveDescendant(child)) {
        return true;
      }
    }
    return false;
  }
```

3. Update the `validateSurface` doc comment ("additionally permits `widget.setRange`") to describe the namespaced rule and tappable cards.

- [ ] **Step 4: Run the protocol and widget model suites**

Run: `flutter test test/domain/models/dynamic_ui test/domain/models/widgets`
Expected: PASS. If an existing test asserted widget mode rejects another namespaced id, change it to assert chat-mode rejection.

- [ ] **Step 5: Commit**

```bash
git add lib/domain/models/dynamic_ui/dynamic_surface_protocol.dart test/fixtures/dynamic_ui/widget_surface_cases.json test/domain/models/dynamic_ui
git commit -m "feat(dynamic-ui): widget-mode tappable cards and namespaced widget actions (ENG-269)"
```

---

### Task B2: `app_ui`: tappable card and the widget icon vocabulary

**Files:**

* Modify: `packages/app_ui/lib/src/theme/app_icons.dart`, `packages/app_ui/test/theme/app_icons_test.dart` (`_vocabulary`)
* Modify: `packages/app_ui/lib/src/components/dynamic_ui/dynamic_surface_catalog.dart`, `dynamic_surface_view.dart` (`_buildCard`, `_buildIcon`)
* Modify: `tool/design_gallery/lib/src/dynamic_ui.dart`
* Test: `packages/app_ui/test/components/dynamic_ui/dynamic_surface_view_test.dart`, `dynamic_surface_catalog_test.dart`

**Interfaces:**

- Produces:
  * `DynamicSurfaceCatalog.icons: Map<String, IconData>` and `DynamicSurfaceCatalog.iconFor(Object? name) -> IconData` (unknown → `AppIcons.unknown`).
  * A `card` with a String `actionId` renders as one focusable, pressable button that dispatches `(actionId, actionValue)`.
- [ ] **Step 1: Write the failing view tests**

Add to `dynamic_surface_view_test.dart` (reusing its `pump`, `c`, `actions` helpers):

```dart
  testWidgets('a card with an action is one button that dispatches', (tester) async {
    await pump(tester, [
      c('card', properties: {
        'title': 'Kitchen',
        'actionId': 'home.toggle',
        'actionValue': 'light.kitchen',
      }, children: [
        c('text', properties: {'text': 'On'}),
      ]),
    ]);

    await tester.tap(find.text('On'));
    expect(actions.single, ('home.toggle', 'light.kitchen'));

    final semantics = tester.getSemantics(find.byType(DynamicSurfaceView));
    expect(
      semantics,
      containsSemantics(isButton: true, label: 'Kitchen\nOn', isFocusable: true),
    );
  });

  testWidgets('a tappable card activates from the keyboard', (tester) async {
    await pump(tester, [
      c('card', properties: {'actionId': 'home.toggle', 'actionValue': 'x'},
          children: [c('text', properties: {'text': 'Fan'})]),
    ]);
    await tester.sendKeyEvent(LogicalKeyboardKey.tab);
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    expect(actions.single.$1, 'home.toggle');
  });

  testWidgets('a card without an action is not a button', (tester) async {
    await pump(tester, [c('card', properties: {'title': 'Plain'})]);
    expect(find.bySemanticsLabel('Plain'), findsOneWidget);
    expect(tester.getSemantics(find.text('Plain')), isNot(containsSemantics(isButton: true)));
  });

  testWidgets('weather and home icons resolve to real glyphs', (tester) async {
    for (final name in ['sun', 'rain', 'lightbulb-off', 'lock', 'pause']) {
      expect(DynamicSurfaceCatalog.iconFor(name), isNot(AppIcons.unknown), reason: name);
    }
    expect(DynamicSurfaceCatalog.iconFor('nonsense'), AppIcons.unknown);
  });
```

(Import `package:flutter/services.dart` for `LogicalKeyboardKey`. If the semantics label joins differently, assert with `label: contains('Kitchen')` rather than weakening the button/focusable assertions.)

Run: `cd packages/app_ui && flutter test test/components/dynamic_ui/dynamic_surface_view_test.dart` → FAIL.

- [ ] **Step 2: Add the glyphs**

In `app_icons.dart` add (each is a verified Lucide name in lucide_icons_flutter 3.1.20):

```dart
  // Widget glyphs (ENG-269). Named by meaning, not by Lucide's name.
  static const IconData sun = LucideIcons.sun;
  static const IconData moon = LucideIcons.moon;
  static const IconData cloud = LucideIcons.cloud;
  static const IconData cloudSun = LucideIcons.cloudSun;
  static const IconData rain = LucideIcons.cloudRain;
  static const IconData snow = LucideIcons.cloudSnow;
  static const IconData storm = LucideIcons.cloudLightning;
  static const IconData fog = LucideIcons.cloudFog;
  static const IconData wind = LucideIcons.wind;
  static const IconData thermometer = LucideIcons.thermometer;
  static const IconData droplets = LucideIcons.droplets;
  static const IconData lightbulb = LucideIcons.lightbulb;
  static const IconData lightbulbOff = LucideIcons.lightbulbOff;
  static const IconData power = LucideIcons.power;
  static const IconData lock = LucideIcons.lock;
  static const IconData unlock = LucideIcons.lockOpen;
  static const IconData fan = LucideIcons.fan;
  static const IconData plug = LucideIcons.plug;
  static const IconData home = LucideIcons.house;
  static const IconData music = LucideIcons.music;
  static const IconData pause = LucideIcons.pause;
  static const IconData skipForward = LucideIcons.skipForward;
  static const IconData skipBack = LucideIcons.skipBack;
  static const IconData volumeDown = LucideIcons.volume1;
  static const IconData volumeUp = LucideIcons.volume2;
  static const IconData calendar = LucideIcons.calendar;
  static const IconData heart = LucideIcons.heartPulse;
```

Add the same 27 entries to `_vocabulary` in `app_icons_test.dart` (`'sun': AppIcons.sun`, ...).

- [ ] **Step 3: Move the icon map into the catalog**

In `dynamic_surface_catalog.dart`:

```dart
  /// Icon names a provider may send, mapped to the app's glyphs. Closed on
  /// purpose: an unknown name draws [AppIcons.unknown], never an error.
  /// Mirrors eve-ai's `eve/widgets/icons.py`; keep both in step by hand.
  static const icons = <String, IconData>{
    'info': AppIcons.info,
    'alert': AppIcons.alert,
    'warning': AppIcons.alert,
    'check': AppIcons.check,
    'success': AppIcons.check,
    'sun': AppIcons.sun,
    'moon': AppIcons.moon,
    'cloud': AppIcons.cloud,
    'cloud-sun': AppIcons.cloudSun,
    'rain': AppIcons.rain,
    'snow': AppIcons.snow,
    'storm': AppIcons.storm,
    'fog': AppIcons.fog,
    'wind': AppIcons.wind,
    'thermometer': AppIcons.thermometer,
    'droplets': AppIcons.droplets,
    'lightbulb': AppIcons.lightbulb,
    'lightbulb-off': AppIcons.lightbulbOff,
    'power': AppIcons.power,
    'lock': AppIcons.lock,
    'unlock': AppIcons.unlock,
    'fan': AppIcons.fan,
    'plug': AppIcons.plug,
    'home': AppIcons.home,
    'music': AppIcons.music,
    'play': AppIcons.play,
    'pause': AppIcons.pause,
    'skip-forward': AppIcons.skipForward,
    'skip-back': AppIcons.skipBack,
    'volume-down': AppIcons.volumeDown,
    'volume-up': AppIcons.volumeUp,
    'calendar': AppIcons.calendar,
    'heart': AppIcons.heart,
  };

  static IconData iconFor(Object? name) => icons[name] ?? AppIcons.unknown;
```

(Import `../../theme/app_icons.dart` and `package:flutter/widgets.dart`.) In `dynamic_surface_view.dart` `_buildIcon`, replace the `switch` with `final icon = DynamicSurfaceCatalog.iconFor(value);`. Keep the `Semantics(label: ...)` but make the label readable: `value is String ? value.replaceAll('-', ' ') : 'icon'`.

- [ ] **Step 4: Make the card tappable**

In `_buildCard`, after building the panel content:

```dart
  final content = Column(/* existing title + children column, unchanged */);
  final actionId = props['actionId'];
  if (actionId is! String) return _Panel(child: content);
  Object? actionValue;
  if (props.containsKey('actionValue')) {
    final (ok, resolved) = scope.resolve(props['actionValue']);
    if (!ok) return null;
    actionValue = resolved;
  }
  return _ActionPanel(
    onActivate: () => scope.dispatch(actionId, actionValue),
    child: content,
  );
```

Add next to `_Panel`:

```dart
/// A card that is itself the control: the whole surface is one button.
///
/// One focus stop, one semantics node (its texts merge into the label), a
/// pressed fill so the tap is acknowledged immediately, a click cursor on
/// desktop, and Enter/Space activation. The validator guarantees no
/// interactive descendants, so there is never a tap target inside a tap
/// target.
class _ActionPanel extends StatefulWidget {
  const _ActionPanel({required this.onActivate, required this.child});

  final VoidCallback onActivate;
  final Widget child;

  @override
  State<_ActionPanel> createState() => _ActionPanelState();
}

class _ActionPanelState extends State<_ActionPanel> {
  bool _pressed = false;
  bool _focused = false;

  void _activate() {
    HapticFeedback.selectionClick();
    widget.onActivate();
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final reduce = MediaQuery.disableAnimationsOf(context);
    return MergeSemantics(
      child: Semantics(
        button: true,
        child: FocusableActionDetector(
          mouseCursor: SystemMouseCursors.click,
          onShowFocusHighlight: (value) => setState(() => _focused = value),
          actions: {
            ActivateIntent: CallbackAction<ActivateIntent>(onInvoke: (_) {
              _activate();
              return null;
            }),
          },
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTapDown: (_) => setState(() => _pressed = true),
            onTapCancel: () => setState(() => _pressed = false),
            onTapUp: (_) => setState(() => _pressed = false),
            onTap: _activate,
            child: AnimatedContainer(
              duration: reduce ? Duration.zero : AppMotion.fast,
              curve: AppMotion.fastCurve,
              constraints: const BoxConstraints(minHeight: 48),
              decoration: BoxDecoration(
                color: _pressed ? colors.raisedHover : colors.raised,
                borderRadius: AppRadii.cardMd,
                border: Border.all(
                  color: _focused ? colors.strokeStrong : colors.stroke,
                  width: _focused ? 2 : 1,
                ),
              ),
              padding: const EdgeInsets.all(AppSpacing.lg),
              child: widget.child,
            ),
          ),
        ),
      ),
    );
  }
}
```

(Import `package:flutter/services.dart` for `HapticFeedback`.)

- [ ] **Step 5: Add gallery specimens**

In `tool/design_gallery/lib/src/dynamic_ui.dart`, add to the `dynamic-surface` entry's `specimens` (using the file's `_s`, `_Surface`, `_c` helpers):

```dart
      _s('widget: tappable entity card', () => _Surface([
        _c('card', {'actionId': 'home.toggle', 'actionValue': 'light.kitchen'}, [
          _c('row', {}, [
            _c('icon', {'name': 'lightbulb'}),
            _c('text', {'text': 'Kitchen'}),
            _c('badge', {'label': 'On'}),
          ]),
        ]),
      ])),
      _s('widget: media controls', () => _Surface([
        _c('column', {}, [
          _c('text', {'text': 'Blue in Green'}),
          _c('text', {'text': 'Miles Davis'}),
          _c('row', {}, [
            _c('button', {'label': 'Previous', 'actionId': 'home.media.previous'}),
            _c('button', {'label': 'Pause', 'actionId': 'home.media.play_pause'}),
            _c('button', {'label': 'Next', 'actionId': 'home.media.next'}),
          ]),
        ]),
      ])),
      _s('widget: weather', () => _Surface([
        _c('column', {}, [
          _c('row', {}, [
            _c('icon', {'name': 'rain'}),
            _c('text', {'text': '12°'}),
            _c('text', {'text': 'Rain'}),
          ]),
          _c('text', {'text': 'Feels like 10°'}),
        ]),
      ])),
```

- [ ] **Step 6: Run the design package suite**

Run: `cd packages/app_ui && flutter analyze && flutter test`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add packages/app_ui tool/design_gallery
git commit -m "feat(app_ui): tappable dynamic cards and widget glyphs (ENG-269)"
```

---

### Task B3: Domain and service: refresh interval, risk overrides, action labels, 403

**Files:**

* Modify: `lib/domain/models/widgets/widget_resource.dart`, `lib/data/services/agent/widget_service.dart`, `lib/data/services/agent/langgraph_widget_service.dart`
* Test: `test/domain/models/widgets/widget_resource_test.dart`, `test/data/services/agent/langgraph_widget_service_test.dart`

**Interfaces:**

- Produces:
  * `WidgetCapabilities.labels: Map<String, String>` (from `actions[].label`; defaults to `{}`), constructor param `labels = const {}`.
  * `WidgetResource.refreshAfter: Duration?` (from `refreshAfterSeconds`, clamped to ≥ 5 s), `WidgetResource.actionRisk: Map<String, WidgetActionRisk>` (from `actionRisk`), both persisted by `toJson`/`fromJson`.
  * `WidgetActionRisk riskFor(String actionId, Object? target, WidgetCapabilities? capabilities)` on `WidgetResource`: override `'$actionId:$target'` → capability → `confirm`.
  * `class WidgetForbidden implements Exception` in `widget_service.dart`; `LangGraphWidgetService.runAction` throws it on 403.
- [ ] **Step 1: Write the failing tests**

Add to `widget_resource_test.dart`:

```dart
  Map<String, Object?> snapshot({Object? refresh, Object? risk}) => {
    'resourceId': 'res-1',
    'kind': 'entity',
    'revision': 1,
    'refreshAfterSeconds': ?refresh,
    'actionRisk': ?risk,
    'view': {'components': [{'id': 'root', 'type': 'column', 'properties': {}, 'children': []}], 'data': {}},
  };

  test('refreshAfter is read and floored at five seconds', () {
    expect(WidgetResource.fromSnapshotJson(snapshot(refresh: 10), title: 'K')!.refreshAfter, const Duration(seconds: 10));
    expect(WidgetResource.fromSnapshotJson(snapshot(refresh: 1), title: 'K')!.refreshAfter, const Duration(seconds: 5));
    expect(WidgetResource.fromSnapshotJson(snapshot(), title: 'K')!.refreshAfter, isNull);
    expect(WidgetResource.fromSnapshotJson(snapshot(refresh: 'soon'), title: 'K')!.refreshAfter, isNull);
  });

  test('risk: per-target override, then capability, then confirm', () {
    final resource = WidgetResource.fromSnapshotJson(
      snapshot(risk: {'home.toggle:lock.front_door': 'confirm'}), title: 'K')!;
    const caps = WidgetCapabilities(kinds: [], actions: {'home.toggle': WidgetActionRisk.safe});

    expect(resource.riskFor('home.toggle', 'light.kitchen', caps), WidgetActionRisk.safe);
    expect(resource.riskFor('home.toggle', 'lock.front_door', caps), WidgetActionRisk.confirm);
    expect(resource.riskFor('home.unknown', 'x', caps), WidgetActionRisk.confirm);
    expect(resource.riskFor('home.toggle', 'light.kitchen', null), WidgetActionRisk.confirm);
  });

  test('refreshAfter and actionRisk survive the cache', () {
    final resource = WidgetResource.fromSnapshotJson(
      snapshot(refresh: 30, risk: {'a.b:x': 'confirm'}), title: 'K')!;
    final back = WidgetResource.fromJson(resource.toJson())!;
    expect(back.refreshAfter, const Duration(seconds: 30));
    expect(back.actionRisk, {'a.b:x': WidgetActionRisk.confirm});
  });

  test('capability labels are parsed', () {
    final caps = WidgetCapabilities.fromJson({
      'protocol': 'provider-resource/1.0',
      'kinds': [],
      'actions': [{'type': 'home.toggle', 'risk': 'safe', 'label': 'Toggle'}],
    })!;
    expect(caps.labels, {'home.toggle': 'Toggle'});
  });
```

(If the repo's Dart version does not support null-aware map entries `?value`, write the map with `if (refresh != null) 'refreshAfterSeconds': refresh`.)

Add to `langgraph_widget_service_test.dart`:

```dart
  test('a 403 on an action is WidgetForbidden, not a generic failure', () async {
    final service = serviceFor(MockClient((request) async => http.Response('{"detail":"forbidden"}', 403)));
    expect(
      service.runAction('res-1', const WidgetAction(type: 'home.toggle', input: {'target': 'x'}, expectedRevision: 1), title: 'K'),
      throwsA(isA<WidgetForbidden>()),
    );
  });
```

Run: `flutter test test/domain/models/widgets test/data/services/agent/langgraph_widget_service_test.dart` → FAIL.

- [ ] **Step 2: Implement**

`WidgetCapabilities`: add `final Map<String, String> labels;` with constructor `{required this.kinds, required this.actions, this.labels = const {}}`; in `fromJson` collect `if (entry['label'] is String) labels[type] = entry['label'] as String;`.

`WidgetResource`: add fields and parsing:

```dart
  /// How long this snapshot stays fresh, from the provider. Null means the
  /// provider did not say; the library then uses its own default.
  final Duration? refreshAfter;

  /// Per-target risk where it differs from the action's advertised default,
  /// keyed `<actionId>:<target>`.
  final Map<String, WidgetActionRisk> actionRisk;

  static const minRefresh = Duration(seconds: 5);

  static Duration? _refresh(Object? raw) {
    if (raw is! int || raw <= 0) return null;
    final value = Duration(seconds: raw);
    return value < minRefresh ? minRefresh : value;
  }

  static Map<String, WidgetActionRisk> _risks(Object? raw) => raw is Map
      ? {
          for (final e in raw.entries)
            if (e.key is String)
              e.key as String: e.value == 'safe' ? WidgetActionRisk.safe : WidgetActionRisk.confirm,
        }
      : const {};

  WidgetActionRisk riskFor(String actionId, Object? target, WidgetCapabilities? capabilities) =>
      actionRisk['$actionId:$target'] ?? capabilities?.actions[actionId] ?? WidgetActionRisk.confirm;
```

Thread both through the constructor (`this.refreshAfter`, `this.actionRisk = const {}`), `fromSnapshotJson` (`refreshAfter: _refresh(raw['refreshAfterSeconds'])`, `actionRisk: _risks(raw['actionRisk'])`), `toJson` (`'refreshAfterSeconds': refreshAfter?.inSeconds`, `'actionRisk': {for (final e in actionRisk.entries) e.key: e.value.name}`), `fromJson` (same parsers) and `copyWith` (carry both).

`widget_service.dart`:

```dart
/// The member lacks the permission this action needs. Distinct from a
/// transport failure so the card can say so instead of offering a retry
/// that will fail the same way.
class WidgetForbidden implements Exception {
  const WidgetForbidden();
}
```

`LangGraphWidgetService.runAction`: in the `on LangGraphException` block, before `_rethrowAsUnsupportedIfMissing`, add `if (error.statusCode == 403) throw const WidgetForbidden();`.

- [ ] **Step 3: Run and commit**

Run: `flutter test test/domain/models/widgets test/data/services/agent/langgraph_widget_service_test.dart test/data/services/widgets` → PASS

```bash
git add lib/domain/models/widgets lib/data/services/agent/widget_service.dart lib/data/services/agent/langgraph_widget_service.dart test/domain/models/widgets test/data/services/agent/langgraph_widget_service_test.dart
git commit -m "feat(widgets): refresh interval, per-target risk and action labels in the widget model (ENG-269)"
```

---

### Task B4: View model: generic actions (and `applyFilters` on the same path)

**Files:**

* Modify: `lib/ui/features/widgets/view_models/widgets_view_model.dart`
* Test: `test/ui/features/widgets/view_models/widgets_view_model_test.dart`

**Interfaces:**

- Consumes: B3.
- Produces on `WidgetsViewModel`:
  * `WidgetCapabilities? get capabilities` (stored by `load()`).
  * `bool isBusy(String id)`.
  * `WidgetActionRisk riskFor(String id, String actionId, Object? target)`.
  * `String? actionLabel(String actionId)` (from capabilities).
  * `Future<void> runAction(String id, String actionId, Object? target)`: ignored while busy; on success adopts the returned snapshot; on `WidgetConflict` adopts `current`; on `WidgetForbidden` sets error `"You don't have permission to do that."` (not stale); on other errors sets `"Couldn't reach it. Try again."` (not stale: the data is not older than before, the action failed).
  * `applyFilters` keeps its behavior and now shares `_submit`.
- [ ] **Step 1: Write the failing tests**

Add to `widgets_view_model_test.dart` (extend `FakeWidgetService` with `WidgetAction? lastAction;` set in `runAction`, and a `Completer<void>? gate;` awaited in `runAction` when non-null):

```dart
  test('a generic action goes to the resource API with its target', () async {
    final service = FakeWidgetService();
    final model = build(service);
    await model.load();

    await model.runAction('res-1', 'home.toggle', 'light.kitchen');

    expect(service.lastAction!.type, 'home.toggle');
    expect(service.lastAction!.input, {'target': 'light.kitchen'});
    expect(service.lastAction!.expectedRevision, 1);
    expect(model.resources.single.revision, 2);
  });

  test('a second tap while busy is ignored', () async {
    final service = FakeWidgetService()..gate = Completer<void>();
    final model = build(service);
    await model.load();

    final first = model.runAction('res-1', 'home.toggle', 'light.kitchen');
    expect(model.isBusy('res-1'), isTrue);
    await model.runAction('res-1', 'home.toggle', 'light.kitchen');
    service.gate!.complete();
    await first;

    expect(service.actionCalls, 1);
    expect(model.isBusy('res-1'), isFalse);
  });

  test('forbidden says so and does not mark the data stale', () async {
    final service = FakeWidgetService();
    final model = build(service);
    await model.load();
    service.nextError = const WidgetForbidden();

    await model.runAction('res-1', 'home.toggle', 'lock.front_door');

    expect(model.errorFor('res-1'), "You don't have permission to do that.");
    expect(model.staleIds, isEmpty);
  });

  test('risk comes from the capabilities load() stored', () async {
    final service = FakeWidgetService()
      ..caps = const WidgetCapabilities(kinds: [], actions: {'home.toggle': WidgetActionRisk.safe}, labels: {'home.toggle': 'Toggle'});
    final model = build(service);
    await model.load();

    expect(model.riskFor('res-1', 'home.toggle', 'light.kitchen'), WidgetActionRisk.safe);
    expect(model.riskFor('res-1', 'home.other', 'x'), WidgetActionRisk.confirm);
    expect(model.actionLabel('home.toggle'), 'Toggle');
  });
```

(Make `FakeWidgetService.capabilities()` return `caps ?? const WidgetCapabilities(kinds: ['chart'], actions: {})`.)

Run → FAIL.

- [ ] **Step 2: Implement**

```dart
  WidgetCapabilities? _capabilities;
  final Set<String> _busy = {};

  WidgetCapabilities? get capabilities => _capabilities;
  bool isBusy(String id) => _busy.contains(id);
  String? actionLabel(String actionId) => _capabilities?.labels[actionId];

  WidgetActionRisk riskFor(String id, String actionId, Object? target) =>
      _find(id)?.riskFor(actionId, target, _capabilities) ?? WidgetActionRisk.confirm;
```

In `load()`: `_capabilities = await service.capabilities();` (inside the existing `try`).

Extract the body of `applyFilters` into one submit path both callers use:

```dart
  /// The one path every widget write takes: generation-guarded, adopting the
  /// provider's truth on success and on conflict. Returns whether it landed.
  Future<bool> _submit(
    String id,
    WidgetAction Function(WidgetResource existing) build, {
    required void Function(Object error) onError,
  }) async {
    final service = _service;
    final existing = _find(id);
    if (service == null || existing == null) return false;
    final cache = _cache;
    final generation = (_generation[id] ?? 0) + 1;
    _generation[id] = generation;
    var landed = false;
    try {
      final fresh = await service.runAction(id, build(existing), title: existing.title);
      if (_generation[id] != generation) return false;
      _replace(fresh);
      _stale.remove(id);
      _errors.remove(id);
      landed = true;
    } on WidgetConflict catch (conflict) {
      if (_generation[id] != generation) return false;
      _replace(conflict.current);
      _stale.remove(id);
      _errors.remove(id);
      landed = true;
    } catch (error) {
      if (_generation[id] != generation) return false;
      onError(error);
    }
    await cache.write(_resources);
    notifyListeners();
    return landed;
  }

  Future<void> applyFilters(String id, Map<String, Object?> filters) async {
    _pendingFilters[id] = Map.of(filters);
    final landed = await _submit(
      id,
      (existing) => WidgetAction(type: 'filters.replace', input: filters, expectedRevision: existing.revision),
      onError: (_) {
        _stale.add(id);
        _errors[id] = 'Could not apply that filter';
      },
    );
    if (landed) _pendingFilters.remove(id);
  }

  /// Runs a provider action against [target]. Never optimistic: the card
  /// shows pending until the provider answers with the device's real state.
  Future<void> runAction(String id, String actionId, Object? target) async {
    if (_busy.contains(id)) return;
    _busy.add(id);
    _errors.remove(id);
    notifyListeners();
    final landed = await _submit(
      id,
      (existing) => WidgetAction(type: actionId, input: {'target': target}, expectedRevision: existing.revision),
      onError: (error) => _errors[id] = error is WidgetForbidden
          ? "You don't have permission to do that."
          : "Couldn't reach it. Try again.",
    );
    _busy.remove(id);
    if (landed) _afterAction(id);
    notifyListeners();
  }

  /// Hook for Task B5 (settle refresh). No-op until live refresh exists.
  void _afterAction(String id) {}
```

Keep the existing `applyFilters` tests passing unchanged (they cover pending filters, conflict adoption and generations).

- [ ] **Step 3: Run and commit**

Run: `flutter test test/ui/features/widgets/view_models/widgets_view_model_test.dart` → PASS

```bash
git add lib/ui/features/widgets/view_models/widgets_view_model.dart test/ui/features/widgets/view_models/widgets_view_model_test.dart
git commit -m "feat(widgets): run provider actions from the widget library (ENG-269)"
```

---

### Task B5: View model: live refresh (interval, backoff, settle after an action)

**Files:**

* Modify: `lib/ui/features/widgets/view_models/widgets_view_model.dart`
* Test: `test/ui/features/widgets/view_models/widgets_live_refresh_test.dart`

**Interfaces:**

- Consumes: `WidgetResource.refreshAfter`, `generatedAt` (B3).
- Produces: constructor param `DateTime Function()? now` (defaults to `DateTime.now`); `void startLive()`, `void stopLive()`, `bool get live`; constants `defaultRefresh = Duration(minutes: 5)`, `maxBackoff = Duration(minutes: 5)`, `settleDelay = Duration(seconds: 2)`; `dispose()` cancels timers.
- [ ] **Step 1: Write the failing tests**

```dart
// test/ui/features/widgets/view_models/widgets_live_refresh_test.dart
import 'package:assistant/data/services/widgets/widget_cache.dart';
import 'package:assistant/domain/models/widgets/widget_resource.dart';
import 'package:assistant/ui/features/widgets/view_models/widgets_view_model.dart';
import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'widgets_view_model_test.dart' show FakeWidgetService, resourceFor;

WidgetResource every(Duration interval, DateTime at) => WidgetResource.fromSnapshotJson({
  'resourceId': 'res-1', 'kind': 'entity', 'revision': 1,
  'refreshAfterSeconds': interval.inSeconds, 'generatedAt': at.toIso8601String(),
  'view': {'components': [{'id': 'r', 'type': 'column', 'properties': {}, 'children': []}], 'data': {}},
}, title: 'K')!;

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('refreshes on the interval only while live', () {
    fakeAsync((async) {
      final service = FakeWidgetService();
      service.onSnapshot = (id) async => every(const Duration(seconds: 10), async.getClock(DateTime(2026)).now());
      final model = WidgetsViewModel(service: service, cache: WidgetCache(scope: 't'),
          now: () => async.getClock(DateTime(2026)).now());
      model.load();
      async.flushMicrotasks();
      final afterLoad = service.snapshotCalls;

      model.startLive();
      async.elapse(const Duration(seconds: 31));
      expect(service.snapshotCalls - afterLoad, 3);

      model.stopLive();
      async.elapse(const Duration(minutes: 5));
      expect(service.snapshotCalls - afterLoad, 3);
      model.dispose();
    });
  });

  test('failures back off, doubling to the cap, and recover', () {
    fakeAsync((async) {
      final service = FakeWidgetService();
      final clock = () => async.getClock(DateTime(2026)).now();
      service.onSnapshot = (id) async => every(const Duration(seconds: 10), clock());
      final model = WidgetsViewModel(service: service, cache: WidgetCache(scope: 't'), now: clock);
      model.load();
      async.flushMicrotasks();
      model.startLive();

      service.onSnapshot = (id) async => throw Exception('down');
      final before = service.snapshotCalls;
      async.elapse(const Duration(seconds: 10)); // fails once, next in 20 s
      async.elapse(const Duration(seconds: 19));
      expect(service.snapshotCalls - before, 1);
      async.elapse(const Duration(seconds: 1)); // 2nd failure, next in 40 s
      expect(service.snapshotCalls - before, 2);
      model.dispose();
    });
  });

  test('a successful action schedules a settle refresh', () {
    fakeAsync((async) {
      final service = FakeWidgetService();
      final model = WidgetsViewModel(service: service, cache: WidgetCache(scope: 't'));
      model.load();
      async.flushMicrotasks();
      model.startLive();
      final before = service.snapshotCalls;

      model.runAction('res-1', 'home.toggle', 'light.kitchen');
      async.flushMicrotasks();
      async.elapse(WidgetsViewModel.settleDelay);

      expect(service.snapshotCalls - before, 1);
      model.dispose();
    });
  });
}
```

Run → FAIL.

- [ ] **Step 2: Implement**

```dart
  static const defaultRefresh = Duration(minutes: 5);
  static const maxBackoff = Duration(minutes: 5);
  static const settleDelay = Duration(seconds: 2);

  final DateTime Function() _now;   // constructor: `DateTime Function()? now` → `_now = now ?? DateTime.now`
  bool _live = false;
  final Map<String, Timer> _timers = {};
  final Map<String, int> _failures = {};

  bool get live => _live;

  /// Keeps visible widgets fresh. The screen calls this when it is shown and
  /// the app is in the foreground, and [stopLive] otherwise: polling a screen
  /// nobody is looking at spends battery and the provider's rate budget.
  void startLive() {
    if (_live) return;
    _live = true;
    for (final resource in _resources) {
      final interval = resource.refreshAfter ?? defaultRefresh;
      final age = resource.generatedAt == null ? interval : _now().difference(resource.generatedAt!);
      _schedule(resource.id, age >= interval ? Duration.zero : interval - age);
    }
  }

  void stopLive() {
    _live = false;
    for (final timer in _timers.values) {
      timer.cancel();
    }
    _timers.clear();
  }

  void _schedule(String id, Duration delay) {
    if (!_live) return;
    _timers.remove(id)?.cancel();
    _timers[id] = Timer(delay, () {
      _timers.remove(id);
      refresh(id);
    });
  }

  Duration _nextDelay(String id) {
    final interval = _find(id)?.refreshAfter ?? defaultRefresh;
    final failures = _failures[id] ?? 0;
    if (failures == 0) return interval;
    final backoff = interval * (1 << failures.clamp(0, 10));
    return backoff > maxBackoff ? maxBackoff : backoff;
  }

  /// Replaces B4's no-op body in place (same private method, not an override):
  /// the device may take a moment to report its new state.
  void _afterAction(String id) => _schedule(id, settleDelay);

  @override
  void dispose() {
    stopLive();
    super.dispose();
  }
```

(`_afterAction` replaces the B4 no-op in place, not an override.) In `refresh()`: on success `_failures.remove(id)`; on failure `_failures[id] = (_failures[id] ?? 0) + 1`; at the end (both branches, after the generation checks) `_schedule(id, _nextDelay(id));`. In `load()`, after the per-summary snapshot loop, `if (_live) { for (final r in _resources) _schedule(r.id, _nextDelay(r.id)); }`. In `remove()`, `_timers.remove(id)?.cancel(); _failures.remove(id);`. Import `dart:async`.

- [ ] **Step 3: Run and commit**

Run: `flutter test test/ui/features/widgets` → PASS

```bash
git add lib/ui/features/widgets/view_models/widgets_view_model.dart test/ui/features/widgets/view_models/widgets_live_refresh_test.dart
git commit -m "feat(widgets): live refresh on the provider's interval with backoff (ENG-269)"
```

---

### Task B6: Card and screen: action routing, pending state, confirmation, masonry layout, lifecycle

**Files:**

* Create: `lib/ui/features/widgets/views/widget_columns.dart`
* Modify: `lib/ui/features/widgets/views/widget_card.dart`, `lib/ui/features/widgets/views/widgets_screen.dart`
* Test: `test/ui/features/widgets/views/widget_card_test.dart`, `test/ui/features/widgets/views/widgets_screen_test.dart`

**Interfaces:**

- Consumes: B2 tappable card, B4/B5 view model.
- Produces:
  * `WidgetCard` gains `required Future<void> Function(String actionId, Object? target) onAction` and `bool busy = false`. `surface.submit` is still dropped; `widget.setRange` still goes to `onRangeChanged`; every other action id goes to `onAction`.
  * `WidgetColumns({required List<Widget> children, double maxColumnWidth = 360, double gap = AppSpacing.lg})`.
  * `WidgetsScreen` confirms `confirm`-risk actions with `showAppDialog`, starts/stops live refresh with visibility and app lifecycle.
- [ ] **Step 1: Write the failing card tests**

Add to `widget_card_test.dart`:

```dart
WidgetResource tappableLight(String id) => WidgetResource.fromSnapshotJson({
  'resourceId': id, 'kind': 'entity', 'revision': 1,
  'view': {'components': [{
    'id': 'root', 'type': 'card',
    'properties': {'actionId': 'home.toggle', 'actionValue': 'light.kitchen'},
    'children': [{'id': 't', 'type': 'text', 'properties': {'text': 'Kitchen'}, 'children': []}],
  }], 'data': {}},
}, title: 'Kitchen')!;

  testWidgets('a provider action is forwarded with its target', (tester) async {
    final calls = <(String, Object?)>[];
    await pumpApp(tester, WidgetCard(
      resource: tappableLight('res-1'), stale: false,
      onRetry: () {}, onDelete: () {}, onRangeChanged: (_) async {},
      onAction: (id, target) async => calls.add((id, target)),
    ));
    await tester.tap(find.text('Kitchen').last);
    expect(calls, [('home.toggle', 'light.kitchen')]);
  });

  testWidgets('while busy the card ignores taps and says it is working', (tester) async {
    final calls = <String>[];
    await pumpApp(tester, WidgetCard(
      resource: tappableLight('res-1'), stale: false, busy: true,
      onRetry: () {}, onDelete: () {}, onRangeChanged: (_) async {},
      onAction: (id, _) async => calls.add(id),
    ));
    await tester.tap(find.text('Kitchen').last, warnIfMissed: false);
    expect(calls, isEmpty);
    expect(find.bySemanticsLabel(RegExp('Working')), findsOneWidget);
  });

  testWidgets('an action error shows without the stale label', (tester) async {
    await pumpApp(tester, WidgetCard(
      resource: tappableLight('res-1'), stale: false, error: "Couldn't reach it. Try again.",
      onRetry: () {}, onDelete: () {}, onRangeChanged: (_) async {}, onAction: (_, _) async {},
    ));
    expect(find.text("Couldn't reach it. Try again."), findsOneWidget);
    expect(find.text('Showing last saved data.'), findsNothing);
  });
```

Update the existing "submit button is dropped" test to pass `onAction` and assert it was not called.

Run → FAIL.

- [ ] **Step 2: Implement the card**

In `widget_card.dart`:

```dart
  final bool busy;
  final Future<void> Function(String actionId, Object? target) onAction;
```

`onRemoteAction` becomes:

```dart
              onRemoteAction: (action) async {
                if (busy) return;
                if (action.actionId == rangeActionId) {
                  final days = int.tryParse('${action.value}');
                  if (days != null) await onRangeChanged(days);
                  return;
                }
                // A chat action has no chat behind a widget; drop it.
                if (action.actionId == 'surface.submit') return;
                await onAction(action.actionId, action.value);
              },
```

Wrap the renderer:

```dart
            Semantics(
              liveRegion: busy,
              label: busy ? 'Working…' : null,
              child: IgnorePointer(
                ignoring: busy,
                child: AnimatedOpacity(
                  opacity: busy ? 0.55 : 1,
                  duration: MediaQuery.disableAnimationsOf(context) ? Duration.zero : AppMotion.fast,
                  child: DynamicSurfaceRenderer(/* unchanged */),
                ),
              ),
            ),
```

Error chrome: show `error` whenever it is non-null; show "Showing last saved data." and the Retry button only when `stale`. (Retry after an action error is the member tapping again, which is clearer than a Retry button whose meaning changes.)

- [ ] **Step 3: Implement** `WidgetColumns`

```dart
// lib/ui/features/widgets/views/widget_columns.dart
import 'dart:math' as math;

import 'package:app_ui/app_ui.dart';
import 'package:flutter/widgets.dart';

/// A masonry of widget cards: as many columns as fit at [maxColumnWidth],
/// cards dealt round-robin so reading order stays left-to-right, top-to-bottom.
///
/// Replaces a fixed-aspect grid, which clipped any widget taller than its
/// cell (a media card with two control rows, a five-day forecast). Widgets
/// are provider-authored and vary in height; the layout must follow them.
class WidgetColumns extends StatelessWidget {
  const WidgetColumns({
    super.key,
    required this.children,
    this.maxColumnWidth = 360,
    this.gap = AppSpacing.lg,
  });

  final List<Widget> children;
  final double maxColumnWidth;
  final double gap;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, constraints) {
      final count = math.max(1, ((constraints.maxWidth + gap) / (maxColumnWidth + gap)).ceil());
      final columns = List.generate(count, (_) => <Widget>[]);
      for (var i = 0; i < children.length; i++) {
        final column = columns[i % count];
        if (column.isNotEmpty) column.add(SizedBox(height: gap));
        column.add(children[i]);
      }
      return Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (var i = 0; i < count; i++) ...[
            if (i > 0) SizedBox(width: gap),
            Expanded(child: Column(mainAxisSize: MainAxisSize.min, children: columns[i])),
          ],
        ],
      );
    });
  }
}
```

- [ ] **Step 4: Write the failing screen tests**

Add to `widgets_screen_test.dart` (using its existing pumping pattern and a fake service with `caps` from B4's fake):

```dart
  testWidgets('a confirm-risk action asks first, and Cancel runs nothing', (tester) async {
    // resource with actionRisk {'home.toggle:lock.front_door': 'confirm'} and a
    // tappable card targeting lock.front_door; capabilities label 'Toggle'.
    await pumpLibrary(tester, service);
    await tester.tap(find.text('Front door').last);
    await tester.pumpAndSettle();

    expect(find.text('Toggle Front door?'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(service.actionCalls, 0);
  });

  testWidgets('a safe action runs on one tap', (tester) async {
    await pumpLibrary(tester, service); // tappable light, safe
    await tester.tap(find.text('Kitchen').last);
    await tester.pumpAndSettle();
    expect(service.actionCalls, 1);
  });

  testWidgets('a tall widget is not clipped', (tester) async {
    await pumpLibrary(tester, serviceWithTallWidget); // 12 text rows
    expect(tester.takeException(), isNull); // no RenderFlex overflow
    expect(find.text('row 11'), findsOneWidget);
  });

  testWidgets('live refresh stops when the screen goes away', (tester) async {
    await pumpLibrary(tester, service);
    final model = tester.element(find.byType(WidgetsScreen)).read<WidgetsViewModel>();
    expect(model.live, isTrue);
    await tester.pumpWidget(const SizedBox());
    expect(model.live, isFalse);
  });
```

(Write `pumpLibrary` alongside the existing helpers: provide a `WidgetsViewModel(service: ..., cache: WidgetCache(scope: 'test'))` and pump `WidgetsScreen` via `pumpApp`. Build the lock/light/tall fixture resources with `WidgetResource.fromSnapshotJson` exactly like `tappableLight` above.)

Run → FAIL.

- [ ] **Step 5: Implement the screen**

In `_WidgetsScreenState`:

```dart
  AppLifecycleListener? _lifecycle;
  WidgetsViewModel? _model;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (!mounted) return;
      _model = context.read<WidgetsViewModel>();
      await _model!.load();
      if (mounted) _model!.startLive();
    });
    // Only poll what someone can see.
    _lifecycle = AppLifecycleListener(
      onShow: () => _model?.startLive(),
      onHide: () => _model?.stopLive(),
    );
  }

  @override
  void dispose() {
    _lifecycle?.dispose();
    _model?.stopLive();
    super.dispose();
  }

  Future<void> _onAction(WidgetsViewModel model, WidgetResource resource, String actionId, Object? target) async {
    if (model.riskFor(resource.id, actionId, target) == WidgetActionRisk.confirm) {
      final verb = model.actionLabel(actionId) ?? 'Continue';
      final confirmed = await showAppDialog(
        context: context,
        title: '$verb ${resource.title}?',
        confirmLabel: verb,
      );
      if (!confirmed || !mounted) return;
    }
    await model.runAction(resource.id, actionId, target);
  }
```

Replace the `GridView.builder` with:

```dart
                  : SingleChildScrollView(
                      padding: const EdgeInsets.all(AppSpacing.xl),
                      child: WidgetColumns(children: [
                        for (final resource in model.resources)
                          WidgetCard(
                            key: ValueKey(resource.id),
                            resource: resource,
                            stale: model.staleIds.contains(resource.id),
                            busy: model.isBusy(resource.id),
                            error: model.errorFor(resource.id),
                            onRetry: () => model.retry(resource.id),
                            onDelete: () => model.remove(resource.id),
                            onRangeChanged: (days) => model.applyFilters(resource.id, {...resource.filters, 'days': days}),
                            onAction: (actionId, target) => _onAction(model, resource, actionId, target),
                          ),
                      ]),
                    ),
```

Keep pull-to-refresh behavior if present (wrap in the existing `RefreshIndicator` if the screen has one; `SingleChildScrollView` needs `physics: const AlwaysScrollableScrollPhysics()` for that).

- [ ] **Step 6: Run the feature suite and analyze**

Run: `flutter analyze && flutter test test/ui/features/widgets test/domain test/data`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add lib/ui/features/widgets test/ui/features/widgets
git commit -m "feat(widgets): interactive, live widget library with confirmation and masonry layout (ENG-269)"
```

---

### Task B7: Client docs

**Files:**

- Modify: `docs/internals/widgets.md`, `AGENTS.md` (§ "Reusable widgets")
- [ ] **Step 1: Update** `docs/internals/widgets.md`

Add sections (and correct the ones they supersede):

- **Actions.** In widget mode the validator accepts any namespaced `actionId` (`^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`) plus `actionId`/`actionValue` on `card`; the provider is the authority on which actions exist. `WidgetCard` routes `widget.setRange` → `filters.replace`, drops `surface.submit`, and sends everything else as `WidgetAction(type: actionId, input: {'target': value})`. Risk resolution: `actionRisk["<action>:<target>"]` → `capabilities.actions[action]` → `confirm`. `confirm` shows `showAppDialog` titled `"<label> <widget title>?"`. Errors: 403 → `WidgetForbidden` ("You don't have permission to do that."), other failures → "Couldn't reach it. Try again."; neither marks the data stale. Never optimistic: the card dims and ignores taps until the provider answers.
- **Tappable cards.** A `card` with an action is one button (merged semantics, focusable, Enter/Space, pressed fill, click cursor). It may not contain interactive descendants (validator rule, `action-schema`).
- **Freshness.** `refreshAfterSeconds` (floored at 5 s, default 5 min) drives per-widget timers while `/widgets` is visible and the app is foregrounded (`AppLifecycleListener`); failures back off ×2 up to 5 min; a successful action schedules a settle refresh after 2 s. Push is ENG-271.
- **Layout.** `WidgetColumns` masonry replaces the fixed-aspect grid because provider-authored widgets vary in height.
- **Shared cases.** `test/fixtures/dynamic_ui/widget_surface_cases.json` is byte-identical to eve-ai's `tests/fixtures/widget_surface_cases.json`; change both together.
- **Icons.** `DynamicSurfaceCatalog.icons` is the closed name→glyph map; unknown names draw the `unknown` glyph.
- [ ] **Step 2: Update** `AGENTS.md`

In "Reusable widgets", replace the sentence about `widget.setRange` being the only permitted id with: "In widget mode any namespaced action id and a tappable `card` are legal (`validateSurface(..., widget: true)`); `WidgetCard` maps `widget.setRange` to `filters.replace` and forwards every other action to `WidgetsViewModel.runAction`, which honours the provider's advertised risk. Widgets poll on the snapshot's `refreshAfterSeconds` only while visible."

- [ ] **Step 3: Commit**

```bash
git add docs/internals/widgets.md AGENTS.md
git commit -m "docs(widgets): document interactive and live widgets (ENG-269)"
```

---

# Part C: End-to-end verification

### Task C1: Run both sides together and walk the acceptance criteria

**Files:** none (verification only). Attach screenshots/recordings to the open-assistant PR (repo rule: UI changes need visual evidence).

- [ ] **Step 1: Start eve-ai against the stub Home Assistant**

```bash
cd ~/GitHub/eve-ai
docker compose -f docker-compose.test.yml up -d
uv run eve-migrate
uv run uvicorn tests.fixtures.stub_home_assistant:app --port 8124 &
EVE_TOOLS_HOME_ASSISTANT_URL=http://localhost:8124 EVE_TOOLS_WEATHER_LATITUDE=49.28 \
  EVE_TOOLS_WEATHER_LONGITUDE=-123.12 uv run aegra dev
```

Before this, extend `tests/fixtures/stub_home_assistant.py` with `lock.front_door` (`locked`) and a `media_player.living_room` state carrying `media_title`, `media_artist`, `volume_level`, and make its `call_service` handle `lock`/`unlock` and the media services (state flips only; `media_play_pause` toggles `playing`/`paused`). Commit that fixture change with Task A7 if you do it earlier.

- [ ] **Step 2: Point the app at it**

Run the app on an emulator (`flutter run`), set `agentCustomEndpoint` to the Aegra URL (`http://10.0.2.2:<port>` from the Android emulator), sign in as a member with `home.control`.

- [ ] **Step 3: Walk every acceptance criterion from ENG-269**

1. In chat, ask for a weather widget, a kitchen light widget, and a living-room speaker widget → three widgets appear under Widgets, built from presets (`kind` = `weather`/`entity`/`media`).
2. Ask "show my next 3 calendar events and today's recovery" → a `custom` widget renders (with the calendar list and the recovery value; an empty calendar shows the `empty` text).
3. Tap anywhere on the light card → it dims briefly, then shows the new state from the action's snapshot; TalkBack announces it as one button.
4. Media: Pause/Next/Louder work; now playing updates within \~10 s of a change made on the stub (`curl -X POST localhost:8124/api/services/media_player/media_next_track -d '{"entity_id":"media_player.living_room"}' -H 'content-type: application/json'`).
5. Save a `lock.front_door` entity widget and tap it → confirmation "Toggle Front door?" appears; Cancel does nothing; Toggle unlocks.
6. Remove `home.control` from the member (family.yaml) and tap the light → "You don't have permission to do that."; the server log shows a 403.
7. Ask for a template that binds a source it did not declare → the tool answer names the problem and nothing is saved.
8. Confirm the Aegra log shows **no model call** during library refreshes (only `GET .../snapshot`).
9. Background the app for a minute → no snapshot requests in the server log; foreground → widgets refresh immediately.

- [ ] **Step 4: Record evidence and open PRs**

Screen recording of steps 3 to 5 for the open-assistant PR; link both PRs to ENG-269.

---

## Self-review notes (resolved inline)

* **Spec coverage:** templates + presets (A6), pluggable sources (A1 to A4) and actions with risk + permission (A7), `/capabilities` generated (A7), weather incl. the broken `home.weather` fix (A2), `home.entity`/`home.media` (A3), records/health on the registry (A1, including a newly found health unwrap bug), tappable card (A5/B1/B2), generic action routing with risk (B3/B4/B6), refresh interval (A7/B5), provider-agnostic client (only namespaced ids, advertised risk/labels), validators in sync + tests (A5/B1 shared cases), docs (A8/B7). Acceptance criteria walked in C1.
* **Deliberate deviations from the ENG-269 text, to confirm with Noah:**
  * `climate` setpoints and `alarm_control_panel` are **not** in the first action set: a setpoint needs a value input beyond a single target, and arming/disarming needs a code. Locks and covers cover the "confirm" class. Both are one `register` call later.
  * Media **artwork** is deferred: widget snapshots may not carry `image` components (existing image-lifetime rule, spec 4.1).
  * `input_boolean` was added to the `safe` toggle domains alongside light/switch/fan.
* **Type consistency checked:** `ActionContext`, `ActionType.risk_for/default_risk/targeted`, `SourceType.targets/item_fields/permissions/ttl_seconds`, `recipe.declared_targets/ttl_seconds`, snapshot keys `refreshAfterSeconds`/`actionRisk`, `WidgetResource.refreshAfter/actionRisk/riskFor`, `WidgetCapabilities.labels`, `WidgetsViewModel.runAction/isBusy/riskFor/actionLabel/startLive/stopLive/settleDelay`, `WidgetCard.onAction/busy`.
