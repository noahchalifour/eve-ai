"""Home Assistant state changes as signals. Pushed, never polled: which
entities are worth Eve's attention is a Home Assistant question, answered in
Home Assistant's own automations (design section 4.4).
"""

from __future__ import annotations

from datetime import UTC, datetime

from eve_ambient.types import Signal


def from_webhook(payload: dict) -> Signal:
    entity_id = str(payload["entity_id"])
    state = str(payload.get("state", "unknown"))
    name = payload.get("friendly_name") or entity_id
    try:
        occurred_at = datetime.fromisoformat(str(payload["occurred_at"]))
    except (KeyError, TypeError, ValueError):
        occurred_at = datetime.now(UTC)
    return Signal(
        source="home",
        # The state is in the key so open -> closed -> open is a new signal,
        # while repeated `open` reports inside the cooldown are one.
        key=f"{entity_id}:{state}",
        occurred_at=occurred_at,
        member_sub=None,
        summary=f"{name} is {state}.",
        payload=payload,
    )
