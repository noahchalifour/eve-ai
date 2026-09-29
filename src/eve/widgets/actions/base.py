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
