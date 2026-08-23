import json
from unittest.mock import AsyncMock

from eve_ambient.sources import finances

TRANSACTIONS = {
    "transactions": [
        {"id": "t1", "amount": -842.19, "merchant": "Dentist", "date": "2026-08-23"},
        {"id": "t2", "amount": -12.40, "merchant": "Coffee", "date": "2026-08-23"},
    ]
}
BUDGETS = {
    "budgets": [
        {"id": "b1", "category": "Groceries", "period": "2026-08", "spent": 910.0, "limit": 800.0},
        {"id": "b2", "category": "Fuel", "period": "2026-08", "spent": 120.0, "limit": 300.0},
    ]
}


def _fake_invoke(**by_tool):
    async def _invoke(tool, args, **kwargs):
        return json.dumps(by_tool.get(tool, {}))

    return AsyncMock(side_effect=_invoke)


async def test_transactions_and_overrun_budgets_both_become_signals(monkeypatch):
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": TRANSACTIONS,
            "finances.get_budgets": BUDGETS,
        }),
    )
    keys = [s.key for s in await finances.poll("")]
    assert "t1" in keys and "t2" in keys
    assert "budget:b1:2026-08:over" in keys


async def test_a_budget_within_its_limit_is_not_a_signal(monkeypatch):
    """Nothing has happened. A signal per budget per poll would burn the
    filter's whole day on non-events."""
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": {"transactions": []},
            "finances.get_budgets": BUDGETS,
        }),
    )
    assert [s.key for s in await finances.poll("")] == ["budget:b1:2026-08:over"]


async def test_a_budget_signal_stays_quiet_for_a_month(monkeypatch):
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": {"transactions": []},
            "finances.get_budgets": BUDGETS,
        }),
    )
    budget = (await finances.poll(""))[0]
    assert budget.cooldown_hours == finances.BUDGET_COOLDOWN_HOURS == 720


async def test_transactions_use_the_default_cooldown(monkeypatch):
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": TRANSACTIONS,
            "finances.get_budgets": {"budgets": []},
        }),
    )
    assert all(s.cooldown_hours is None for s in await finances.poll(""))


async def test_signals_are_household_scoped(monkeypatch):
    """Money is shared; the audience comes from the filter, not the account."""
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": TRANSACTIONS,
            "finances.get_budgets": {"budgets": []},
        }),
    )
    assert all(s.member_sub is None for s in await finances.poll(""))


async def test_one_failing_call_does_not_lose_the_other(monkeypatch):
    async def _invoke(tool, args, **kwargs):
        if tool == "finances.get_budgets":
            return "error: monarch unavailable"
        return json.dumps(TRANSACTIONS)

    monkeypatch.setattr(finances, "invoke", AsyncMock(side_effect=_invoke))
    assert [s.key for s in await finances.poll("")] == ["t1", "t2"]
