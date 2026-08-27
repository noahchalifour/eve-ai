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

import logging
from datetime import UTC, datetime
from functools import lru_cache

from gql import gql
from monarchmoney import MonarchMoney, MonarchMoneyEndpoints

# Monarch moved its API off api.monarchmoney.com, which now answers a 301
# to api.monarch.com. aiohttp rewrites a redirected POST into a GET, so the
# login endpoint answered 405 and every call failed with something that
# looked nothing like "the domain changed". monarchmoney 0.1.15 is the
# latest release and still carries the old host, so it is corrected here.
# Remove this when a release ships with the new base URL.
MonarchMoneyEndpoints.BASE_URL = "https://api.monarch.com"

from eve_tools.settings import get_tools_settings

logger = logging.getLogger(__name__)

_logged_in = False


def _is_number(value: object) -> bool:
    """`bool` is an `int` subclass; a budget field that came back `True`
    should not pass as a spend amount."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@lru_cache(maxsize=1)
def _client() -> MonarchMoney:
    return MonarchMoney()


def _authenticate_with_token(client: MonarchMoney, token: str) -> None:
    """Adopt an existing Monarch session token.

    `set_token` on its own is not enough: the library sets the token *and*
    the Authorization header together everywhere it authenticates for real
    (`_login_user`, `load_session`), and its public setter only does half of
    that. So the header is set here as well. Reaching into `_headers` is
    deliberate, and `test_a_token_sets_the_authorization_header` exists so a
    library upgrade that renames it fails in CI rather than at the first
    poll after a deploy.
    """
    client.set_token(token)
    client._headers["Authorization"] = f"Token {token}"


async def _authenticated() -> MonarchMoney:
    global _logged_in
    client = _client()
    if not _logged_in:
        settings = get_tools_settings()
        if settings.monarch_token:
            # A token is what the client uses for every request anyway, and
            # it is the only option for an account created through Google
            # sign-in, which has no password. It also sidesteps MFA.
            _authenticate_with_token(client, settings.monarch_token)
        elif settings.monarch_email and settings.monarch_password:
            await client.login(
                email=settings.monarch_email,
                password=settings.monarch_password,
                mfa_secret_key=settings.monarch_mfa_secret or None,
            )
        else:
            raise RuntimeError(
                "no Monarch credentials: set EVE_TOOLS_MONARCH_TOKEN (preferred, "
                "and the only option for a Google sign-in account), or both "
                "EVE_TOOLS_MONARCH_EMAIL and EVE_TOOLS_MONARCH_PASSWORD"
            )
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


# monarchmoney's own get_budgets sends one kitchen-sink GetJointPlanningData
# query - goals, goal contributions, category groups, rollover periods - and
# Monarch now answers all of it with a 500 ("Something went wrong while
# processing"), whatever the goals flags are set to. The three fields this
# normalizer actually reads work fine on their own, so ask for only those.
# Selecting the category name here also replaces a second round trip
# (get_transaction_categories) that existed only because the library's query
# asks for `category { id }` without the name.
_BUDGETS_QUERY = gql(
    """
    query Budgets($startDate: Date!, $endDate: Date!) {
      budgetData(startMonth: $startDate, endMonth: $endDate) {
        monthlyAmountsByCategory {
          category { id name }
          monthlyAmounts { month plannedCashFlowAmount actualAmount }
        }
      }
    }
    """
)


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

    Live Monarch returns both fields positive for an expense category
    (planned 900.0, actual 860.82). Comparing absolute magnitudes
    (`spent = abs(actual)`, `limit = abs(planned)`) still holds if a
    category ever signs them the other way, so the magnitudes stay.
    """
    client = await _authenticated()
    target_month = month or datetime.now(UTC).strftime("%Y-%m")
    # startMonth == endMonth: the row this normalizer wants is the target
    # month's, and a wider window only returns rows the loop below drops.
    first_of_month = f"{target_month}-01"
    raw = await client.gql_call(
        "Budgets",
        _BUDGETS_QUERY,
        {"startDate": first_of_month, "endDate": first_of_month},
    )
    budget_data = raw.get("budgetData") or {}
    budgets = []
    for entry in budget_data.get("monthlyAmountsByCategory") or []:
        if not isinstance(entry, dict):
            continue
        # The query always requests `id`, and live responses carry it - but
        # a truthy non-dict `category` (a list, a string) must still not
        # raise out of a plain `.get`.
        category = entry.get("category")
        if category is not None and not isinstance(category, dict):
            logger.warning("budget entry had a non-dict category, dropping it: %r", category)
            continue
        category_id = (category or {}).get("id")
        if not category_id:
            continue
        name = (category or {}).get("name")
        if not name:
            # The query asks for `name`, so a real response should never omit
            # it - which is exactly why it needs to be visible if it happens
            # rather than silently degrading this category to its raw id.
            logger.warning(
                "category %r has no name; its budget will show the id instead", category_id
            )
            name = category_id
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
            # `limit > 0` is normalization - a zero or negative planned
            # amount isn't a budget at all. Filtering to only *overrun*
            # categories is not: `get_budgets` is still exposed
            # conversationally as "Read current budget and cash-flow
            # summary" (fix round 4, item 3), so dropping every
            # within-budget category here made "how are we doing on
            # groceries?" answer `{"budgets": []}` unless the family was over.
            # That filter belongs to, and already lives in,
            # `eve_ambient.sources.finances._budget_overruns`.
            if not limit > 0:
                continue
            budgets.append(
                {
                    "id": f"{category_id}:{target_month}",
                    "category": name,
                    "period": target_month,
                    "spent": spent,
                    "limit": limit,
                }
            )
    return {"budgets": budgets}
