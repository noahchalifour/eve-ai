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
from eve.widgets import actions, presets, store
from eve.widgets import recipe as recipe_rules
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
