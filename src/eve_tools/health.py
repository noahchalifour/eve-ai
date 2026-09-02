"""Fan-out across whichever health providers a member has connected.

Knows both providers exist; knows neither's wire format. The clients return
bare lists so this layer owns the envelope - which is what makes "the member
has both devices" a merge rather than a special case in each client.

Also the seam a future `eve_ambient.sources.health` reads through, the way
`eve_ambient.sources.finances` reads `monarch.get_budgets`: the shapes here
are deliberately what a signal source would want, so adding one needs no
reshaping (spec section 8).
"""

from __future__ import annotations

import asyncio
import logging

from eve_tools import oauth_store, oura, whoop

logger = logging.getLogger(__name__)

# Trend questions are why a window exists at all. Past two weeks the answer
# belongs to a chart, not a conversation.
MAX_DAYS = 14

_CLIENTS = {"whoop": whoop, "oura": oura}


def _clamp_days(days: object) -> int:
    """1..MAX_DAYS. Clamped here rather than trusted: `days` arrives from a
    model, and "900" must not become 900 days of provider traffic."""
    try:
        value = int(days)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1
    return max(1, min(MAX_DAYS, value))


async def _fan_out(metric: str, key: str, member_sub: str, days: object) -> dict:
    window = _clamp_days(days)
    connected = await oauth_store.configured_providers(member_sub)
    targets = [name for name in _CLIENTS if name in connected]

    async def _one(name: str) -> tuple[str, list[dict] | str]:
        try:
            return name, await getattr(_CLIENTS[name], metric)(member_sub, window)
        except oauth_store.ReconnectRequired as exc:
            # Must never degrade to an empty list: "no sleep data" would have
            # the coach describing a quiet night that never happened.
            return name, f"{name}: {exc}"
        except oauth_store.NotConnected as exc:
            return name, f"{name}: {exc}"
        except Exception as exc:
            logger.warning("%s %s failed for %s", name, metric, member_sub, exc_info=True)
            return name, f"{name}: {exc.__class__.__name__}: {exc}"

    entries: list[dict] = []
    errors: list[str] = []
    for name, outcome in await asyncio.gather(*(_one(n) for n in targets)):
        if isinstance(outcome, str):
            errors.append(outcome)
        else:
            entries.extend(outcome)

    # Newest first across providers. Two devices give two entries per date,
    # each labelled by `source`; the specialist reports both rather than
    # choosing between them.
    entries.sort(key=lambda e: (e.get("date") or "", e.get("source") or ""), reverse=True)

    result: dict = {key: entries}
    unconfigured = sorted(name for name in _CLIENTS if name not in connected)
    if unconfigured:
        result["unconfigured"] = unconfigured
    if errors:
        result["errors"] = errors
    return result


async def get_recovery(member_sub: str, days: int = 1) -> dict:
    return await _fan_out("get_recovery", "recovery", member_sub, days)


async def get_sleep(member_sub: str, days: int = 1) -> dict:
    return await _fan_out("get_sleep", "sleep", member_sub, days)


async def get_activity(member_sub: str, days: int = 1) -> dict:
    return await _fan_out("get_activity", "activity", member_sub, days)
