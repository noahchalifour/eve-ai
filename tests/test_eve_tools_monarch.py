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
    fake_client.login.assert_awaited_once_with(
        email="family@example.com", password="hunter2", mfa_secret_key=None
    )


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


class _FakeMonarch:
    """Enough of MonarchMoney to observe how it was authenticated."""

    def __init__(self):
        self._token = None
        self._headers = {}
        self.login = AsyncMock()

    def set_token(self, token):
        self._token = token


async def test_a_token_authenticates_without_logging_in(monkeypatch):
    """A Monarch account created through Google sign-in has no password, so a
    session token is the only way in - and it is what every request uses
    anyway."""
    monkeypatch.setenv("EVE_TOOLS_MONARCH_TOKEN", "tok-abc123")
    fake = _FakeMonarch()
    monkeypatch.setattr(monarch, "_client", lambda: fake)
    client = await monarch._authenticated()
    assert client is fake
    fake.login.assert_not_awaited()


async def test_a_token_sets_the_authorization_header(monkeypatch):
    """`set_token` alone leaves the header unset, so every request would go
    out unauthenticated. If a library upgrade renames `_headers`, this is the
    test that says so - rather than the first poll after a deploy."""
    monkeypatch.setenv("EVE_TOOLS_MONARCH_TOKEN", "tok-abc123")
    fake = _FakeMonarch()
    monkeypatch.setattr(monarch, "_client", lambda: fake)
    await monarch._authenticated()
    assert fake._token == "tok-abc123"
    assert fake._headers["Authorization"] == "Token tok-abc123"


async def test_a_token_wins_over_email_and_password(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_MONARCH_TOKEN", "tok-abc123")
    fake = _FakeMonarch()
    monkeypatch.setattr(monarch, "_client", lambda: fake)
    await monarch._authenticated()
    fake.login.assert_not_awaited()


async def test_email_and_password_are_still_used_when_no_token_is_set(monkeypatch):
    monkeypatch.delenv("EVE_TOOLS_MONARCH_TOKEN", raising=False)
    fake = _FakeMonarch()
    monkeypatch.setattr(monarch, "_client", lambda: fake)
    await monarch._authenticated()
    fake.login.assert_awaited_once()
    assert fake._headers == {}


async def test_no_credentials_at_all_names_both_ways_in(monkeypatch):
    """The library's own message only mentions email and password, which sends
    a Google sign-in user looking for a password that does not exist."""
    monkeypatch.delenv("EVE_TOOLS_MONARCH_TOKEN", raising=False)
    monkeypatch.setenv("EVE_TOOLS_MONARCH_EMAIL", "")
    monkeypatch.setenv("EVE_TOOLS_MONARCH_PASSWORD", "")
    monkeypatch.setattr(monarch, "_client", lambda: _FakeMonarch())
    with pytest.raises(RuntimeError, match="EVE_TOOLS_MONARCH_TOKEN"):
        await monarch._authenticated()


def test_the_api_base_url_is_the_current_monarch_domain():
    """api.monarchmoney.com 301s to api.monarch.com, and aiohttp turns a
    redirected POST into a GET — so the login endpoint answers 405 and the
    failure looks nothing like a moved domain. Pin the host so an upgrade
    that reintroduces the old one fails here."""
    from monarchmoney import MonarchMoneyEndpoints

    assert MonarchMoneyEndpoints.BASE_URL == "https://api.monarch.com"
    assert MonarchMoneyEndpoints.getLoginEndpoint().startswith("https://api.monarch.com/")


async def test_the_mfa_secret_is_passed_to_login(monkeypatch):
    """With MFA on, Monarch answers a bare password login with 403 and nothing
    here can answer a prompt."""
    monkeypatch.delenv("EVE_TOOLS_MONARCH_TOKEN", raising=False)
    monkeypatch.setenv("EVE_TOOLS_MONARCH_MFA_SECRET", "JBSWY3DPEHPK3PXP")
    fake = _FakeMonarch()
    monkeypatch.setattr(monarch, "_client", lambda: fake)
    await monarch._authenticated()
    assert fake.login.await_args.kwargs["mfa_secret_key"] == "JBSWY3DPEHPK3PXP"


async def test_no_mfa_secret_passes_none_rather_than_an_empty_string(monkeypatch):
    """`oathtool.generate_otp("")` would be asked for a code the account does
    not use; the library only skips TOTP when the key is falsy-as-None."""
    monkeypatch.delenv("EVE_TOOLS_MONARCH_TOKEN", raising=False)
    monkeypatch.delenv("EVE_TOOLS_MONARCH_MFA_SECRET", raising=False)
    fake = _FakeMonarch()
    monkeypatch.setattr(monarch, "_client", lambda: fake)
    await monarch._authenticated()
    assert fake.login.await_args.kwargs["mfa_secret_key"] is None
