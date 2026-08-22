from unittest.mock import AsyncMock, patch

import pytest

from eve_tools import monarch


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_MONARCH_EMAIL", "family@example.com")
    monkeypatch.setenv("EVE_TOOLS_MONARCH_PASSWORD", "hunter2")
    monarch._logged_in = False
    monarch._client.cache_clear()


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
