from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from eve_tools import monarch


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_MONARCH_EMAIL", "family@example.com")
    monkeypatch.setenv("EVE_TOOLS_MONARCH_PASSWORD", "hunter2")
    monarch._logged_in = False
    monarch._client.cache_clear()


def _current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _fake_client(monthly_amounts_by_category, categories=()):
    fake_client = AsyncMock()
    fake_client.login = AsyncMock()
    fake_client.get_budgets = AsyncMock(
        return_value={"budgetData": {"monthlyAmountsByCategory": monthly_amounts_by_category}}
    )
    fake_client.get_transaction_categories = AsyncMock(
        return_value={"categories": list(categories)}
    )
    return fake_client


async def test_list_transactions_filters_by_category():
    fake_client = AsyncMock()
    fake_client.login = AsyncMock()
    fake_client.get_transactions = AsyncMock(
        return_value={
            "allTransactions": {
                "results": [
                    {"id": "1", "category": {"name": "Groceries"}},
                    {"id": "2", "category": {"name": "Gas"}},
                ]
            }
        }
    )
    with patch("eve_tools.monarch._client", return_value=fake_client):
        result = await monarch.list_transactions(limit=20, category="Groceries")
    assert [t["id"] for t in result["transactions"]] == ["1"]
    fake_client.login.assert_awaited_once_with(email="family@example.com", password="hunter2")


async def test_get_budgets_normalizes_an_overrun_category_for_the_current_month():
    month = _current_month()
    fake_client = _fake_client(
        [
            {
                "category": {"id": "cat-groceries"},
                "monthlyAmounts": [
                    {
                        "month": f"{month}-01",
                        "plannedCashFlowAmount": -800.0,
                        "actualAmount": -910.0,
                    }
                ],
            }
        ],
        categories=[{"id": "cat-groceries", "name": "Groceries"}],
    )
    with patch("eve_tools.monarch._client", return_value=fake_client):
        result = await monarch.get_budgets()
    assert result == {
        "budgets": [
            {
                "id": f"cat-groceries:{month}",
                "category": "Groceries",
                "period": month,
                "spent": 910.0,
                "limit": 800.0,
            }
        ]
    }


async def test_a_category_within_its_budget_is_still_normalized_into_a_budget():
    """(fix round 4, item 3) This test used to assert the opposite: that a
    category under its limit was dropped entirely. `get_budgets` is still
    exposed conversationally as "Read current budget and cash-flow summary,"
    so asking Eve "how are we doing on groceries?" must not answer
    `{"budgets": []}` just because nobody is over. Filtering to only
    overruns is `eve_ambient.sources.finances._budget_overruns`'s job, not
    this normalizer's - the `limit > 0` guard here is normalization (a
    category with no real budget isn't one at all); comparing `spent` to
    `limit` is not."""
    month = _current_month()
    fake_client = _fake_client(
        [
            {
                "category": {"id": "cat-fuel"},
                "monthlyAmounts": [
                    {
                        "month": f"{month}-01",
                        "plannedCashFlowAmount": -300.0,
                        "actualAmount": -120.0,
                    }
                ],
            }
        ]
    )
    with patch("eve_tools.monarch._client", return_value=fake_client):
        result = await monarch.get_budgets()
    assert result == {
        "budgets": [
            {
                "id": f"cat-fuel:{month}",
                "category": "cat-fuel",
                "period": month,
                "spent": 120.0,
                "limit": 300.0,
            }
        ]
    }


async def test_a_category_with_no_planned_amount_is_skipped():
    month = _current_month()
    fake_client = _fake_client(
        [
            {
                "category": {"id": "cat-misc"},
                "monthlyAmounts": [
                    {"month": f"{month}-01", "plannedCashFlowAmount": None, "actualAmount": -50.0}
                ],
            }
        ]
    )
    with patch("eve_tools.monarch._client", return_value=fake_client):
        result = await monarch.get_budgets()
    assert result == {"budgets": []}


async def test_a_month_outside_the_current_one_is_skipped():
    fake_client = _fake_client(
        [
            {
                "category": {"id": "cat-groceries"},
                "monthlyAmounts": [
                    {
                        "month": "1999-01-01",
                        "plannedCashFlowAmount": -800.0,
                        "actualAmount": -910.0,
                    }
                ],
            }
        ],
        categories=[{"id": "cat-groceries", "name": "Groceries"}],
    )
    with patch("eve_tools.monarch._client", return_value=fake_client):
        result = await monarch.get_budgets()
    assert result == {"budgets": []}


async def test_a_missing_category_name_falls_back_to_the_id():
    month = _current_month()
    fake_client = _fake_client(
        [
            {
                "category": {"id": "cat-unknown"},
                "monthlyAmounts": [
                    {
                        "month": f"{month}-01",
                        "plannedCashFlowAmount": -800.0,
                        "actualAmount": -910.0,
                    }
                ],
            }
        ]
    )
    with patch("eve_tools.monarch._client", return_value=fake_client):
        result = await monarch.get_budgets()
    [budget] = result["budgets"]
    assert budget["category"] == "cat-unknown"


async def test_a_category_record_present_but_missing_a_name_falls_back_to_the_id(caplog):
    """Distinct from the case above: here the category id *is* in the
    lookup, just without a "name" field - the exact shape that raised
    KeyError before _category_names guarded it. The degradation must be
    logged: this is the one shape in the phase unverified against a live
    account, and a silent fallback here would look identical to a quiet
    month with nobody over budget."""
    month = _current_month()
    fake_client = _fake_client(
        [
            {
                "category": {"id": "cat-weird"},
                "monthlyAmounts": [
                    {
                        "month": f"{month}-01",
                        "plannedCashFlowAmount": -800.0,
                        "actualAmount": -910.0,
                    }
                ],
            }
        ],
        categories=[{"id": "cat-weird"}],
    )
    with patch("eve_tools.monarch._client", return_value=fake_client):
        with caplog.at_level("WARNING"):
            result = await monarch.get_budgets()
    [budget] = result["budgets"]
    assert budget["category"] == "cat-weird"
    assert "cat-weird" in caplog.text


async def test_a_non_dict_category_does_not_raise(caplog):
    month = _current_month()
    fake_client = _fake_client(
        [
            {
                "category": ["not", "a", "dict"],
                "monthlyAmounts": [
                    {
                        "month": f"{month}-01",
                        "plannedCashFlowAmount": -800.0,
                        "actualAmount": -910.0,
                    }
                ],
            }
        ]
    )
    with patch("eve_tools.monarch._client", return_value=fake_client):
        with caplog.at_level("WARNING"):
            result = await monarch.get_budgets()
    assert result == {"budgets": []}
    assert "non-dict category" in caplog.text


async def test_an_explicit_month_pins_which_row_matches():
    """Deriving the expected month through the same datetime.now() call the
    normalizer uses would never catch a month-boundary bug; an explicit
    month makes the assertion independent of when the test runs."""
    fake_client = _fake_client(
        [
            {
                "category": {"id": "cat-groceries"},
                "monthlyAmounts": [
                    {
                        "month": "2020-05-01",
                        "plannedCashFlowAmount": -800.0,
                        "actualAmount": -910.0,
                    }
                ],
            }
        ],
        categories=[{"id": "cat-groceries", "name": "Groceries"}],
    )
    with patch("eve_tools.monarch._client", return_value=fake_client):
        result = await monarch.get_budgets(month="2020-05")
    assert result == {
        "budgets": [
            {
                "id": "cat-groceries:2020-05",
                "category": "Groceries",
                "period": "2020-05",
                "spent": 910.0,
                "limit": 800.0,
            }
        ]
    }


async def test_an_explicit_month_that_does_not_match_is_skipped():
    fake_client = _fake_client(
        [
            {
                "category": {"id": "cat-groceries"},
                "monthlyAmounts": [
                    {
                        "month": "2020-05-01",
                        "plannedCashFlowAmount": -800.0,
                        "actualAmount": -910.0,
                    }
                ],
            }
        ],
        categories=[{"id": "cat-groceries", "name": "Groceries"}],
    )
    with patch("eve_tools.monarch._client", return_value=fake_client):
        result = await monarch.get_budgets(month="2020-06")
    assert result == {"budgets": []}
