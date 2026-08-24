import json
from unittest.mock import AsyncMock

import pytest

from eve_ambient.sources import finances
from eve_ambient.types import SourceUnavailable

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


async def test_a_failing_budgets_call_now_propagates_instead_of_silently_losing_it(
    monkeypatch,
):
    """(fix round 4, item 2) This test used to assert that a failing
    `finances.get_budgets` call left the transactions half of `poll`
    untouched - which only held because `_budget_overruns` swallowed
    eve-tools' `error:` string into `[]`, indistinguishable from "nothing is
    over budget." `poll_once` already isolates and counts a raising member
    per source per tick, so `finances.poll` now raises instead, and the
    whole household-scoped poll for this tick counts as failed rather than
    quietly reporting a partial result that looks identical to a healthy
    one."""
    async def _invoke(tool, args, **kwargs):
        if tool == "finances.get_budgets":
            return "error: monarch unavailable"
        return json.dumps(TRANSACTIONS)

    monkeypatch.setattr(finances, "invoke", AsyncMock(side_effect=_invoke))
    with pytest.raises(SourceUnavailable):
        await finances.poll("")


async def test_a_non_dict_transaction_is_skipped_not_raised(monkeypatch):
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": {"transactions": ["t1", "t2"]},
            "finances.get_budgets": {"budgets": []},
        }),
    )
    assert await finances.poll("") == []


async def test_a_non_dict_budget_is_skipped_not_raised(monkeypatch):
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": {"transactions": []},
            "finances.get_budgets": {"budgets": ["b1", "b2"]},
        }),
    )
    assert await finances.poll("") == []


async def test_a_non_numeric_spent_or_limit_is_skipped(monkeypatch):
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": {"transactions": []},
            "finances.get_budgets": {
                "budgets": [
                    {"id": "b3", "category": "Rent", "period": "2026-08",
                     "spent": "a lot", "limit": 800.0},
                ]
            },
        }),
    )
    assert await finances.poll("") == []


async def test_a_null_transaction_id_does_not_collide_with_a_missing_id(monkeypatch):
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": {
                "transactions": [
                    {"id": None, "amount": -5.0, "merchant": "Coffee", "date": "2026-08-23"},
                    {"amount": -6.0, "merchant": "Coffee", "date": "2026-08-23"},
                ]
            },
            "finances.get_budgets": {"budgets": []},
        }),
    )
    assert await finances.poll("") == []


async def test_a_missing_or_unparseable_date_falls_back_to_now_without_raising(monkeypatch):
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": {
                "transactions": [
                    {"id": "t1", "amount": -5.0, "merchant": "Coffee"},
                    {"id": "t2", "amount": -6.0, "merchant": "Coffee", "date": "not-a-date"},
                ]
            },
            "finances.get_budgets": {"budgets": []},
        }),
    )
    keys = [s.key for s in await finances.poll("")]
    assert keys == ["t1", "t2"]


async def test_an_offset_date_is_converted_to_utc_not_relabelled(monkeypatch):
    """.replace(tzinfo=UTC) on an offset-bearing timestamp would silently
    shift the instant; astimezone(UTC) converts it."""
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": {
                "transactions": [
                    {
                        "id": "t1",
                        "amount": -5.0,
                        "merchant": "Coffee",
                        "date": "2026-08-23T12:00:00-07:00",
                    }
                ]
            },
            "finances.get_budgets": {"budgets": []},
        }),
    )
    [signal] = await finances.poll("")
    assert signal.occurred_at.hour == 19
    assert signal.occurred_at.utcoffset().total_seconds() == 0


async def test_a_non_list_transactions_container_yields_no_signals(monkeypatch):
    """`{"transactions": 5}` is truthy, so `or []` never fires; the `for`
    statement itself would raise TypeError without a type check on the
    container, not just on its members."""
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": {"transactions": 5},
            "finances.get_budgets": {"budgets": []},
        }),
    )
    assert await finances.poll("") == []


async def test_a_non_list_budgets_container_yields_no_signals(monkeypatch):
    """`{"budgets": 5}` is truthy and non-iterable, so `or []` never fires
    and the bare `for` statement itself would raise TypeError. (A string
    would not discriminate this: it is iterable, so the unfixed code would
    have walked its characters, each failing the per-item dict check, and
    still returned [] - see the case below for that.)"""
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": {"transactions": []},
            "finances.get_budgets": {"budgets": 5},
        }),
    )
    assert await finances.poll("") == []


async def test_a_string_budgets_container_is_handled_sanely_rather_than_iterated(monkeypatch):
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": {"transactions": []},
            "finances.get_budgets": {"budgets": "not-a-list"},
        }),
    )
    assert await finances.poll("") == []


async def test_a_nested_merchant_object_is_flattened_to_its_name(monkeypatch):
    """The real Monarch payload nests merchant as {"name", "id", ...}, not a
    flat string."""
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": {
                "transactions": [
                    {
                        "id": "t1",
                        "amount": -5.0,
                        "merchant": {"name": "Coffee Shop", "id": "merch-1"},
                        "date": "2026-08-23",
                    }
                ]
            },
            "finances.get_budgets": {"budgets": []},
        }),
    )
    [signal] = await finances.poll("")
    assert "Coffee Shop" in signal.summary
    assert "{" not in signal.summary
