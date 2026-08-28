"""Building the two dataset shapes.

Inputs come from Eve's own Postgres tables and one hand-authored golden file,
never from parsed Langfuse traces (eval design 4.1, ADR 0009). Trace shape is
set by Aegra and LiteLLM and changes when either is upgraded; these tables are
ours.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import yaml

from eve.eval.types import DatasetItem
from eve.settings import get_settings

logger = logging.getLogger(__name__)

_REQUIRED_SIGNAL_KEYS = ("source", "key", "occurred_at", "summary")


def build_turns(path: str) -> list[DatasetItem]:
    """The hand-authored golden set. Small and reviewed like code, because it
    is the definition of 'working' the A/B measures against."""
    with open(path) as handle:
        raw = yaml.safe_load(handle) or []
    return [
        DatasetItem(
            id=entry["id"],
            shape="turns",
            input={"member": entry["member"], "message": entry["message"]},
            expected={"expects": entry["expects"]},
            canary=bool(entry.get("canary", False)),
        )
        for entry in raw
    ]


def ambient_items_from_rows(rows: list[dict]) -> list[DatasetItem]:
    """Shape a decision row into an item, skipping anything unreplayable.

    A signal blob that will not rehydrate into a Signal is skipped rather than
    raised on: one malformed row from an old deploy must not make the whole
    dataset unbuildable.
    """
    items = []
    for row in rows:
        signal = row.get("signal") or {}
        if not all(signal.get(key) for key in _REQUIRED_SIGNAL_KEYS):
            logger.warning("skipping decision %s: unreplayable signal", row.get("id"))
            continue
        verdict = row.get("verdict") or {}
        items.append(
            DatasetItem(
                id=str(row["id"]),
                shape="ambient",
                input={"signal": signal},
                expected={
                    "notify": bool(verdict.get("notify", False)),
                    "audience": list(verdict.get("audience") or []),
                    "urgent": bool(verdict.get("urgent", False)),
                    "replied": bool(row.get("replied", False)),
                    "notices": int(row.get("notices") or 0),
                },
            )
        )
    return items


async def build_ambient(limit: int | None = None) -> list[DatasetItem]:
    """Read decisions inside the retention window and shape them."""
    from eve_ambient.store import decisions_since

    settings = get_settings()
    since = datetime.now(UTC) - timedelta(days=settings.eval_decision_retention_days)
    rows = await decisions_since(since, limit or settings.eval_dataset_limit)
    return ambient_items_from_rows(rows)
