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
