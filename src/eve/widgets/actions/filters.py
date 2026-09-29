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
