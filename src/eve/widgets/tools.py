"""The one way a widget is created.

Everything the model supplies is checked here, and everything it must NOT
supply - the owner, the credentials, the endpoints - is injected or absent by
construction. A recipe that passes this gate runs on every later refresh with
no model and no human in the loop, which is why the checks are in this order:
kind, then recipe shape, then permissions, then storage.
"""

from __future__ import annotations

import logging
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from eve.specialists.permissions import permission_denial
from eve.state import EveState, turn_is_ambient
from eve.widgets import recipe as recipe_rules
from eve.widgets import store

logger = logging.getLogger(__name__)

_DESCRIPTION = """Save a reusable widget the member can open later from Widgets.

The widget refreshes its own data every time it is opened, without asking you
again, so the `recipe` must say WHERE the data comes from rather than
containing the data itself.

`recipe` is {"sources": [...], "metric": {...}}. A source is either
{"type": "records", "collection": "<a collection you have recorded into>"} or
{"type": "health", "metric": "recovery"|"sleep"|"activity"}. A metric is
{"op": "count"} or {"op": "sum"|"avg"|"max", "field": "<payload field>"}.

Save a widget when the member wants to keep looking at something. Answer in
prose for a one-off question."""


@tool(description=_DESCRIPTION)
async def save_widget(
    title: str,
    kind: str,
    recipe: dict,
    state: Annotated[EveState, InjectedState],
    config: RunnableConfig,
    filters: dict | None = None,
) -> str:
    configurable = config.get("configurable") or {}
    member = configurable.get("member") or {}
    member_sub = member["sub"]

    # An ambient turn is composed from a webhook payload, not spoken by the
    # member, and the ambient credential can impersonate anyone. It cannot
    # create a durable resource in someone's account. Keyed off the marker
    # `compose_prompt` really emits, via the one shared predicate: nothing
    # sets `configurable["is_ambient"]`, so checking that was inert (EVE-30).
    if turn_is_ambient(state.get("messages") or []):
        return "A widget cannot be created from an ambient turn."

    if kind not in recipe_rules.KINDS:
        legal = ", ".join(sorted(recipe_rules.KINDS))
        return f"Unknown widget kind {kind!r}. Legal kinds: {legal}."

    if len(title) > recipe_rules.MAX_NAME:
        return (
            f"The widget title is too long: {len(title)} characters, "
            f"the limit is {recipe_rules.MAX_NAME}."
        )

    error = recipe_rules.validate(recipe)
    if error is not None:
        return (
            f"The widget recipe was rejected: {error}. "
            "A source is {\"type\": \"records\", \"collection\": ...} or "
            "{\"type\": \"health\", \"metric\": ...}; nothing else is legal."
        )

    chosen_filters = filters or {}
    filter_error = recipe_rules.validate_filters(chosen_filters)
    if filter_error is not None:
        return f"The widget filters were rejected: {filter_error}."

    for required in recipe_rules.required_permissions(recipe):
        denial = permission_denial(member.get("permissions", []), required)
        if denial:
            return denial

    try:
        created = await store.create(
            member_sub, kind, title, recipe, chosen_filters
        )
    except Exception as exc:
        logger.warning("save_widget failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"

    return (
        f"Saved the widget {title!r} (id {created['id']}). "
        "It appears under Widgets and refreshes itself when opened."
    )