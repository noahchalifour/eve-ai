"""Unread mail as signals, via eve-tools' existing Gmail client."""

from __future__ import annotations

from datetime import UTC, datetime

from eve.tools_client import invoke
from eve_ambient.types import Signal, tool_result

_QUERY = "is:unread newer_than:1d"


def _occurred_at(message: dict) -> datetime:
    """Gmail's internalDate is epoch milliseconds as a string. A missing or
    malformed value must not lose the signal, so it falls back to now."""
    try:
        return datetime.fromtimestamp(int(message["internalDate"]) / 1000, UTC)
    except (KeyError, TypeError, ValueError):
        return datetime.now(UTC)


async def poll(member_sub: str) -> list[Signal]:
    result = tool_result(
        await invoke("mail.list_messages", {"member_sub": member_sub, "query": _QUERY})
    )
    if result is None:
        return []
    signals = []
    for message in result.get("messages") or []:
        sender = message.get("from", "unknown sender")
        subject = message.get("subject", "(no subject)")
        snippet = message.get("snippet", "")
        signals.append(
            Signal(
                source="mail",
                key=str(message.get("id", "")),
                occurred_at=_occurred_at(message),
                member_sub=member_sub,
                summary=f"Unread mail from {sender}: {subject}. {snippet}".strip(),
                payload=message,
            )
        )
    return [s for s in signals if s.key]
