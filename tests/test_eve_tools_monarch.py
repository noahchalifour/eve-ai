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


async def test_a_category_within_its_budget_is_not_normalized_into_a_budget():
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
    assert result == {"budgets": []}


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
