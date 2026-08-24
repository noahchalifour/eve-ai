"""Unread mail as signals, via eve-tools' existing Gmail client."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from eve.tools_client import invoke
from eve_ambient.types import Signal, list_field, tool_result

logger = logging.getLogger(__name__)

_QUERY = "is:unread newer_than:1d"


def _occurred_at(message: dict) -> datetime:
    """Gmail's internalDate is epoch milliseconds as a string. A missing or
    malformed value must not lose the signal, so it falls back to now. A
    wildly out-of-range value raises OverflowError/OSError out of
    fromtimestamp rather than KeyError/TypeError/ValueError, so this needs
    to catch broadly to make good on that fallback."""
    try:
        return datetime.fromtimestamp(int(message["internalDate"]) / 1000, UTC)
    except Exception:
        return datetime.now(UTC)


async def poll(member_sub: str) -> list[Signal]:
    result = tool_result(
        await invoke("mail.list_messages", {"member_sub": member_sub, "query": _QUERY})
    )
    if result is None:
        return []
    signals = []
    for message in list_field(result, "messages"):
        if not isinstance(message, dict):
            logger.warning("mail.list_messages returned a non-dict message: %r", message)
            continue
        key = str(message.get("id") or "")
        if not key:
            logger.warning("mail message missing an id, dropping it: %r", message)
            continue
        # `or`, not a dict default: gmail.py's hydration fills a missing
        # header with "", which is present-but-falsy, not absent.
        sender = message.get("from") or "unknown sender"
        subject = message.get("subject") or "(no subject)"
        snippet = message.get("snippet", "")
        signals.append(
            Signal(
                source="mail",
                key=key,
                occurred_at=_occurred_at(message),
                member_sub=member_sub,
                summary=f"Unread mail from {sender}: {subject}. {snippet}".strip(),
                payload=message,
            )
        )
    return signals
