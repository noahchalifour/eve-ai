"""Is this request actually from Linear, and is it recent?

Pure functions, no IO. This is the whole of the 5-second response path: a
webhook that cannot be verified must be refused without touching the
database, a model, or the network.

TWO CHECKS, NOT ONE. The signature stops a forged request. The timestamp
stops a replayed genuine one. Neither is redundant: a captured valid POST
replays forever against signature checking alone.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60


def verify_signature(raw_body: bytes, header: str | None, secret: str) -> bool:
    """`raw_body` is the exact bytes received, never a re-serialization of
    parsed JSON: Linear signs what it sent, and `json.dumps` of the parsed
    object differs in whitespace and key order.

    Returns False rather than raising on every malformed input. A raise here
    becomes a 500, and a 500 tells Linear to retry a request that should have
    been refused permanently.
    """
    if not secret or not header:
        return False
    try:
        presented = bytes.fromhex(header)
    except ValueError:
        # Not hex at all. Includes the non-ASCII case, which would make
        # `compare_digest` raise TypeError on a str operand.
        return False
    computed = hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()
    return hmac.compare_digest(computed, presented)


def timestamp_is_fresh(
    webhook_timestamp_ms: int | None,
    now_ms: int | None = None,
    window_seconds: int = _WINDOW_SECONDS,
) -> bool:
    """Linear's `webhookTimestamp` is UNIX milliseconds. Absolute difference,
    so a clock ahead of ours is refused the same as one behind: both mean we
    cannot reason about freshness."""
    if webhook_timestamp_ms is None:
        return False
    try:
        sent = int(webhook_timestamp_ms)
    except (TypeError, ValueError):
        return False
    current = now_ms if now_ms is not None else int(time.time() * 1000)
    return abs(current - sent) <= window_seconds * 1000
