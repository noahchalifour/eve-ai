"""Monarch Money client via the community-maintained `monarchmoney` package -
Monarch has no public documented API, and reverse-engineering its GraphQL
auth flow by hand would be a second maintenance burden for no benefit over
a client that already handles session persistence.

MFA is not handled here: the Monarch account this deployment uses should
either have MFA disabled or a persisted session provisioned out-of-band
(Task 17's provisioning note). A homelab-scale, five-person deployment does
not need an interactive MFA prompt mid-conversation.
"""

from __future__ import annotations

from functools import lru_cache

from monarchmoney import MonarchMoney

from eve_tools.settings import get_tools_settings

_logged_in = False


@lru_cache(maxsize=1)
def _client() -> MonarchMoney:
    return MonarchMoney()


async def _authenticated() -> MonarchMoney:
    global _logged_in
    client = _client()
    if not _logged_in:
        settings = get_tools_settings()
        await client.login(email=settings.monarch_email, password=settings.monarch_password)
        _logged_in = True
    return client


async def list_transactions(limit: int, category: str | None) -> dict:
    client = await _authenticated()
    result = await client.get_transactions(limit=limit)
    transactions = result.get("allTransactions", {}).get("results", [])
    if category:
        transactions = [
            t for t in transactions if (t.get("category") or {}).get("name") == category
        ]
    return {"transactions": transactions}


async def get_budgets() -> dict:
    client = await _authenticated()
    return await client.get_budgets()
