"""Transactions and budget overruns as signals, via eve-tools' Monarch client.

Household-scoped: every signal carries `member_sub=None` and the audience is
the filter's decision (design section 5).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from eve.tools_client import invoke
from eve_ambient.types import Signal, list_field, tool_result

logger = logging.getLogger(__name__)

# A budget that is over stays over for the rest of the month. Six hours would
# mean four notifications a day about one fact.
BUDGET_COOLDOWN_HOURS = 720

_TRANSACTION_LIMIT = 50


def _is_number(value: object) -> bool:
    """`bool` is an `int` subclass; a spent/limit value that came back
    `True` should not pass as a dollar amount."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parsed_date(raw: str | None) -> datetime:
    """A date already carrying an offset (`...-07:00`) is converted to UTC,
    not relabelled as UTC - `.replace` on an offset-bearing value would
    silently shift the instant by however many hours the offset is."""
    try:
        parsed = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return datetime.now(UTC)
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _transactions() -> list[Signal]:
    result = tool_result(
        await invoke("finances.list_transactions", {"limit": _TRANSACTION_LIMIT})
    )
    if result is None:
        return []
    signals = []
    for txn in list_field(result, "transactions"):
        if not isinstance(txn, dict):
            logger.warning("finances.list_transactions returned a non-dict item: %r", txn)
            continue
        key = str(txn.get("id") or "")
        if not key:
            logger.warning("transaction missing an id, dropping it: %r", txn)
            continue
        amount = txn.get("amount")
        # Monarch nests the merchant (`merchant: {name, id, ...}`), not a
        # flat string.
        raw_merchant = txn.get("merchant")
        merchant = (raw_merchant.get("name") if isinstance(raw_merchant, dict) else None) or (
            "unknown merchant"
        )
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
    for budget in list_field(result, "budgets"):
        if not isinstance(budget, dict):
            logger.warning("finances.get_budgets returned a non-dict item: %r", budget)
            continue
        spent, limit = budget.get("spent"), budget.get("limit")
        if not _is_number(spent) or not _is_number(limit):
            logger.warning("budget has a non-numeric spent/limit, dropping it: %r", budget)
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
