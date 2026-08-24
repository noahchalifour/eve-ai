"""Shapes only. No I/O, no behaviour beyond parsing one string."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Signal:
    source: str
    key: str
    occurred_at: datetime
    member_sub: str | None
    summary: str
    # compare=False: a dict is unhashable, and the frozen dataclass's
    # generated __hash__ would otherwise include this field and raise the
    # first time anything puts a Signal in a set (design 4.5 dedups there).
    payload: dict = field(default_factory=dict, compare=False)
    # None means "the configured default". A source that knows its signal
    # should stay quiet longer than six hours says so here (design 4.3).
    cooldown_hours: int | None = None


# Pydantic, not a dataclass: this is the structured-output schema handed to
# the REFLEX model, the same way memory/types.py's Extraction is.
class FilterVerdict(BaseModel):
    notify: bool = False
    audience: list[str] = Field(
        default_factory=list, description="Family member subs to notify."
    )
    urgent: bool = False
    why: str = Field(default="", description="One sentence of reasoning.")


class SourceUnavailable(Exception):
    """Raised by a source's `poll` when `tool_result` returned `None` - the
    eve-tools call failed (an `error:` string) or answered with something
    that wasn't parseable JSON.

    Before this fix (round 4, item 2), every source call site treated that
    `None` the same way it treats a genuinely empty result: `return []`. No
    real source raises on an upstream failure, so "eve-tools cannot reach the
    calendar" and "the calendar has nothing to report" were indistinguishable
    to `app.poll_once` - which means the expected state of a deployment's
    very first enabled tick (every Phase-4 credential still a placeholder)
    primed every source against a poll that never actually ran. `poll_once`
    already isolates and counts a raising member per member (design and fix
    round 1), so raising here changes nothing about steady state - only
    priming, which needs to be able to tell the two states apart."""


class SourcePollError(Exception):
    """Raised by a source's `poll` when part of its work succeeded and part
    failed - `finances.poll` gathers transactions and budget overruns via
    two independent eve-tools calls, and one persistently failing must not
    silently discard the other's signals for as long as the outage lasts
    (fix round 4, item 2 follow-up: Monarch down for the budgets call would
    otherwise mean transaction signals are never emitted at all; they are
    not lost permanently, since nothing marks them seen, but they age out of
    the `limit=50` transaction window eventually, and a family stops hearing
    about large transactions with nothing in the logs saying why beyond a
    recurring warning).

    Carries whatever signals were gathered before the failure, in `partial`,
    so `app.poll_once` can still act on them: an *unprimed* source must not
    prime on any upstream failure, partial or total - `partial` here is no
    more trustworthy as "everything there is to prime" than an empty list
    would be - but an *already-primed* source should still receive whatever
    it did manage to fetch, rather than losing it to the other half's
    outage. `poll_once`'s priming branch already `continue`s without
    touching `signals` when a member fails, so it ignores `partial` the same
    way it ignores everything else on an unprimed tick; only the normal
    (already-primed) branch processes it.

    A source with a single upstream call has no partial result to carry and
    keeps raising the plain `SourceUnavailable` - this exception exists only
    for the two-calls-in-one-poll case."""

    def __init__(self, message: str, partial: list) -> None:
        super().__init__(message)
        self.partial = partial


def tool_result(raw: str) -> dict | None:
    """Unwrap what `eve.tools_client.invoke` returns.

    It answers a JSON string on success and a human-readable `error: ...`
    string on failure, because its usual caller hands the value straight to a
    model. Ambient needs structure, so anything that is not parseable JSON is
    a failure here, not data.
    """
    if raw.startswith("error:"):
        logger.warning("eve-tools reported: %s", raw)
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("eve-tools returned unparseable JSON: %.80s", raw)
        return None
    return parsed if isinstance(parsed, dict) else None


def list_field(container: dict, key: str) -> list:
    """Read a list-shaped field out of a `tool_result` dict.

    `container.get(key) or []` - the pattern every source loop used before
    this helper existed - guards a missing key or an explicit falsy value,
    but not a truthy non-list. `{"messages": 5}` would make the `for`
    statement itself raise `TypeError: 'int' object is not iterable`,
    straight out of a source that a bad upstream payload must not be able
    to kill. A wrong container type is exactly the kind of upstream shape
    change worth seeing in the logs rather than inferring from silence.
    """
    value = container.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        logger.warning(
            "expected a list at %r, got %s instead: %r", key, type(value).__name__, value
        )
        return []
    return value
