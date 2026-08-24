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

from datetime import UTC, datetime
from functools import lru_cache

from monarchmoney import MonarchMoney

from eve_tools.settings import get_tools_settings

_logged_in = False


def _is_number(value: object) -> bool:
    """`bool` is an `int` subclass; a budget field that came back `True`
    should not pass as a spend amount."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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


async def _category_names(client: MonarchMoney) -> dict[str, str]:
    """The budget query selects only `category { id }` - no name - so a
    signal built straight from it would read "Budget over: 1a2b3c". Look
    names up separately and let a missing one fall back to the id rather
    than raising.

    The category fragment always requests `name`, so a real response
    should never omit it - possibly null, never absent - but A2 is the one
    shape in this phase unverified against a live account, so a category
    record missing "name" entirely is excluded here (falling back to the
    id in get_budgets) instead of raising KeyError.
    """
    categories = await client.get_transaction_categories()
    return {
        category["id"]: category["name"]
        for category in categories.get("categories") or []
        if isinstance(category, dict) and category.get("id") and category.get("name")
    }


async def get_budgets(month: str | None = None) -> dict:
    """Monarch's raw shape is `budgetData.monthlyAmountsByCategory[]`, each
    entry a category id paired with one `monthlyAmounts` row per month in
    the (default) last-month-to-next-month range - no flat `spent`/`limit`/
    `period`, no top-level `budgets` list. Normalize to exactly what
    `eve_ambient.sources.finances` expects, and narrow to one calendar
    month: a signal about last month's budget, or a preview of next
    month's, is not a signal about anything that happened.

    `month` (`YYYY-MM`) defaults to the current calendar month and exists
    as an injection point for tests to pin month-boundary behaviour rather
    than deriving the expected value through the same `datetime.now()`
    call. The tool table calls this with no argument, so every real caller
    still gets the current month.

    Sign convention is unverified against a live Monarch account: expense
    transactions are negative, and `plannedCashFlowAmount` for an expense
    category is plausibly negative the same way. Comparing absolute
    magnitudes (`spent = abs(actual)`, `limit = abs(planned)`) is correct
    regardless of which way the API actually signs these fields, which is
    why the comparison uses magnitudes rather than a guessed sign.
    """
    client = await _authenticated()
    raw = await client.get_budgets()
    names = await _category_names(client)

    target_month = month or datetime.now(UTC).strftime("%Y-%m")
    budget_data = raw.get("budgetData") or {}
    budgets = []
    for entry in budget_data.get("monthlyAmountsByCategory") or []:
        if not isinstance(entry, dict):
            continue
        # The category fragment always requests `id`, but A2 is unverified
        # against a live account - a truthy non-dict `category` (a list, a
        # string) must not raise out of a plain `.get`.
        category = entry.get("category")
        category_id = category.get("id") if isinstance(category, dict) else None
        if not category_id:
            continue
        for monthly in entry.get("monthlyAmounts") or []:
            if not isinstance(monthly, dict):
                continue
            entry_month = str(monthly.get("month") or "")
            if not entry_month.startswith(target_month):
                continue
            planned = monthly.get("plannedCashFlowAmount")
            actual = monthly.get("actualAmount")
            if not _is_number(planned) or not _is_number(actual):
                continue
            spent, limit = abs(actual), abs(planned)
            if not (limit > 0 and spent > limit):
                continue
            budgets.append(
                {
                    "id": f"{category_id}:{target_month}",
                    "category": names.get(category_id, category_id),
                    "period": target_month,
                    "spent": spent,
                    "limit": limit,
                }
            )
    return {"budgets": budgets}
