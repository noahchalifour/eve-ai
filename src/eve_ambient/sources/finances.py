"""Transactions and budget overruns as signals, via eve-tools' Monarch client.

Household-scoped: every signal carries `member_sub=None` and the audience is
the filter's decision (design section 5).
"""

from __future__ import annotations

from datetime import UTC, datetime

from eve.tools_client import invoke
from eve_ambient.types import Signal, tool_result

# A budget that is over stays over for the rest of the month. Six hours would
# mean four notifications a day about one fact.
BUDGET_COOLDOWN_HOURS = 720

_TRANSACTION_LIMIT = 50


def _parsed_date(raw: str | None) -> datetime:
    try:
        return datetime.fromisoformat(str(raw)).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


async def _transactions() -> list[Signal]:
    result = tool_result(
        await invoke("finances.list_transactions", {"limit": _TRANSACTION_LIMIT})
    )
    if result is None:
        return []
    signals = []
    for txn in result.get("transactions") or []:
        key = str(txn.get("id", ""))
        if not key:
            continue
        amount = txn.get("amount")
        merchant = txn.get("merchant", "unknown merchant")
        signals.append(
            Signal(
                source="finances",
                key=key,
                occurred_at=_parsed_date(txn.get("date")),
                member_sub=None,
                summary=f"Transaction: {amount} at {merchant} on {txn.get('date')}.",
                payload=txn,
            )
        )
    return signals


async def _budget_overruns() -> list[Signal]:
    result = tool_result(await invoke("finances.get_budgets", {}))
    if result is None:
        return []
    signals = []
    for budget in result.get("budgets") or []:
        spent, limit = budget.get("spent"), budget.get("limit")
        if not isinstance(spent, (int, float)) or not isinstance(limit, (int, float)):
            continue
        if spent <= limit:
            continue
        period = budget.get("period", "")
        signals.append(
            Signal(
                source="finances",
                # The state is in the key, so crossing back under and over
                # again is a new signal rather than a suppressed one.
                key=f"budget:{budget.get('id')}:{period}:over",
                occurred_at=datetime.now(UTC),
                member_sub=None,
                summary=(
                    f"Budget over: {budget.get('category')} for {period} is at "
                    f"{spent} against a limit of {limit}."
                ),
                payload=budget,
                cooldown_hours=BUDGET_COOLDOWN_HOURS,
            )
        )
    return signals


async def poll(member_sub: str) -> list[Signal]:
    """`member_sub` is unused: finances are household-scoped. The parameter
    exists so every source in the registry has one shape."""
    return [*await _transactions(), *await _budget_overruns()]
