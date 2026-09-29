"""The dashboard builder: a stated purpose in, a laid-out dashboard out.

Reached by a stateless run with `config.configurable.dashboard_setup =
{deviceId, purpose, columns}` (`graph._route_after_context`). Like `openers`
it is the whole turn: no VOICE call, no answer, nothing appended to a thread.

Every tile is a LIBRARY widget. The builder first looks at what the member
has already saved and reuses anything that fits the purpose, and saves any
widget it still needs into the library through the exact guards `save_widget`
applies (`eve.widgets.tools.prepare`). The dashboard stores only references
and placement, so replacing or resetting it never deletes a widget.

Progress and the outcome go out as `custom` frames:

    {"dashboard_setup": {"phase": "Choosing widgets"}}
    {"dashboard_setup": {"done": {"revision": 3, "tiles": 4}}}
    {"dashboard_setup": {"error": "..."}}

The last frame is always `done` or `error`, so a client never has to infer
the outcome from the stream simply ending.
"""

from __future__ import annotations

import asyncio
import logging

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.config import get_stream_writer
from langgraph.errors import GraphRecursionError

from eve.dashboards import layout as grid
from eve.dashboards import sizes
from eve.dashboards import store as dashboards
from eve.models import Tier, get_model
from eve.state import EveState
from eve.tools_client import invoke
from eve.widgets import store as widgets
from eve.widgets.tools import _describe as describe_widgets
from eve.widgets.tools import prepare

logger = logging.getLogger(__name__)

MAX_PURPOSE = 280
MAX_ROUNDS = 12
TIMEOUT_SECONDS = 150

_OPENAI_DEVELOPER_ROLE = {"__openai_role__": "developer"}

SYSTEM_PROMPT = """You build a dashboard: a small grid of live widgets on one of the member's devices.

The member told you what the dashboard is for. Choose 2 to 6 widgets that serve exactly that purpose, most
important first (the first lands top-left and largest).

1. Call list_library_widgets. REUSE any saved widget that fits the purpose with use_widget: the member
   already chose it once.
2. For anything still missing, save a new widget with save_dashboard_widget. It goes into the member's
   Widgets library too. Prefer a preset. Never guess a Home Assistant entity id: call find_home_entities
   first and use only ids it returned.
3. Stop calling tools once the dashboard has what it needs. Do not add filler: two good widgets beat six
   loose ones.

A widget that is rejected tells you why; fix it or pick something else.

How to write a widget:
{widget_help}"""


def requested(config: RunnableConfig | None) -> dict | None:
    """The validated `dashboard_setup` request, or None for a normal turn.

    Fails CLOSED to a normal turn only when the key is absent. A present but
    malformed request still routes here, so the builder can answer it with an
    `error` frame instead of the VOICE model answering an empty input."""
    configurable = (config or {}).get("configurable") or {}
    value = configurable.get("dashboard_setup")
    return value if isinstance(value, dict) else None


def _emit(frame: dict) -> None:
    try:
        writer = get_stream_writer()
    except RuntimeError:
        logger.debug("no runnable context for the dashboard_setup frame")
        return
    try:
        writer({"dashboard_setup": frame})
    except Exception:
        logger.warning("could not emit a dashboard_setup frame", exc_info=True)


def _is_ambient(config: RunnableConfig) -> bool:
    principal = (config.get("configurable") or {}).get("langgraph_auth_user")
    if isinstance(principal, dict):
        return principal.get("ambient") is True
    return getattr(principal, "ambient", False) is True


def _parse(request: dict) -> tuple[str, str, int] | str:
    device_id = request.get("deviceId")
    purpose = request.get("purpose")
    columns = request.get("columns")
    if not grid.valid_device_id(device_id):
        return "That device id is not valid."
    if not isinstance(purpose, str) or not purpose.strip():
        return "Tell me what the dashboard is for."
    if len(purpose) > MAX_PURPOSE:
        return f"Keep the purpose under {MAX_PURPOSE} characters."
    if isinstance(columns, bool) or columns not in grid.COLUMN_CHOICES:
        return "That grid size is not supported."
    return device_id, purpose.strip(), columns


def build_tools(member: dict, chosen: list[dict]) -> list[BaseTool]:
    """The builder's tools, closed over the member and the running choice.

    `chosen` is the only output: tool calls append to it, and the node lays
    it out afterwards. The member comes from `load_context` (the
    authenticated principal), never from anything the model supplied."""
    sub = member["sub"]

    def _take(resource: dict) -> str:
        if any(entry["resourceId"] == resource["id"] for entry in chosen):
            return f"{resource['title']!r} is already on the dashboard."
        if len(chosen) >= grid.MAX_TILES:
            return "The dashboard is full."
        allowed, default = sizes.allowed_sizes(resource["kind"])
        chosen.append({
            "resourceId": resource["id"], "title": resource["title"], "kind": resource["kind"],
            "sizes": allowed, "default": default,
        })
        return f"Added {resource['title']!r} ({len(chosen)} so far)."

    @tool
    async def list_library_widgets() -> str:
        """The widgets the member has already saved: id, kind and title."""
        rows = await widgets.list_for(sub)
        if not rows:
            return "The library is empty."
        return "\n".join(f"- {row['id']} [{row['kind']}] {row['title']}" for row in rows)

    @tool
    async def use_widget(resource_id: str) -> str:
        """Put an existing library widget on the dashboard, by its id."""
        resource = await widgets.get(sub, resource_id)
        if resource is None:
            return "No widget with that id is in the library."
        return _take(resource)

    @tool
    async def save_dashboard_widget(
        title: str,
        preset: str | None = None,
        options: dict | None = None,
        sources: dict | None = None,
        template: list | None = None,
    ) -> str:
        """Save a NEW widget into the member's library and put it on the dashboard. Same arguments as save_widget."""
        prepared = prepare(member, title, preset, options, sources, template, None)
        if isinstance(prepared, str):
            return prepared
        kind, recipe, filters = prepared
        created = await widgets.create(sub, kind, title, recipe, filters)
        return _take(created)

    @tool
    async def find_home_entities(domain: str | None = None) -> str:
        """Home Assistant entities with their ids, optionally one domain (light, media_player, lock, climate...)."""
        if "home.control" not in member.get("permissions", []):
            return "This member may not use Home Assistant widgets."
        return await invoke("home.list_entities", {"domain": domain})

    return [list_library_widgets, use_widget, save_dashboard_widget, find_home_entities]


async def build(
    member: dict, purpose: str, model_factory=get_model
) -> list[dict]:
    """The builder's choice, in importance order. Raises on a model failure."""
    chosen: list[dict] = []
    agent = create_agent(
        model_factory(Tier.MECHANICAL),
        build_tools(member, chosen),
        system_prompt=SystemMessage(
            SYSTEM_PROMPT.format(widget_help=describe_widgets()),
            additional_kwargs=_OPENAI_DEVELOPER_ROLE,
        ),
    )
    try:
        await agent.ainvoke(
            {"messages": [HumanMessage(f"The dashboard is for: {purpose}")]},
            {"recursion_limit": 2 * MAX_ROUNDS + 2},
        )
    except GraphRecursionError:
        # Out of rounds, but whatever it chose so far is still a dashboard.
        logger.warning("dashboard builder ran out of rounds with %d tiles", len(chosen))
    return chosen


def place(chosen: list[dict], columns: int) -> list[dict]:
    shapes = [grid.SIZES[entry["default"]] for entry in chosen]
    spots = grid.pack(shapes, columns)
    return [
        {"resourceId": entry["resourceId"], "sizes": entry["sizes"],
         "x": x, "y": y, "w": w, "h": h}
        for entry, (x, y), (w, h) in zip(chosen, spots, shapes)
    ]


def make_node(model_factory=get_model):
    async def dashboard_setup(state: EveState, config: RunnableConfig) -> dict:
        request = requested(config) or {}
        parsed = _parse(request)
        if isinstance(parsed, str):
            _emit({"error": parsed})
            return {}
        if _is_ambient(config):
            _emit({"error": "A dashboard cannot be built by the ambient service."})
            return {}
        device_id, purpose, columns = parsed
        member = state["member"]

        _emit({"phase": "Choosing widgets"})
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                chosen = await build(member, purpose, model_factory)
        except Exception:
            logger.warning("dashboard builder failed", exc_info=True)
            _emit({"error": "Couldn't build the dashboard. Try again."})
            return {}
        if not chosen:
            _emit({"error": "I couldn't find widgets for that. Try describing it differently."})
            return {}

        _emit({"phase": "Laying out"})
        try:
            saved = await dashboards.replace(
                member["sub"], device_id, purpose, columns, place(chosen, columns)
            )
        except Exception:
            logger.warning("dashboard save failed", exc_info=True)
            _emit({"error": "Couldn't save the dashboard. Try again."})
            return {}
        _emit({"done": {"revision": saved["revision"], "tiles": len(chosen)}})
        return {}

    return dashboard_setup

