"""Oura v2 client. Plain httpx, documented REST.

Two things differ from WHOOP and both simplify life here:

- Oura attributes every row to a local `day` string, so there is no timezone
  arithmetic to get wrong (spec 4.3.3).
- Its tokens are long-lived, and if Personal Access Tokens still work
  (spec 1.1) a row may have no refresh token at all. `oauth_store` already
  treats that as an ordinary row, so nothing here special-cases it.

One thing is harder: `daily_readiness` exposes only CONTRIBUTOR scores -
0-100 sub-ratings, not raw measurements. Raw HRV and resting heart rate live
in the detailed `sleep` collection, so recovery is a two-request join. Reading
`contributors.hrv_balance` as an HRV in milliseconds would report a rating as
a measurement.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx

from eve_tools import oauth_store
from eve_tools.settings import get_tools_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ouraring.com/v2/usercollection"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
PROVIDER = "oura"


async def _refresh(refresh_token: str) -> dict:
    settings = get_tools_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.oura_client_id,
                "client_secret": settings.oura_client_secret,
            },
        )
        response.raise_for_status()
        return response.json()


async def _get(member_sub: str, path: str, params: dict) -> dict:
    """One GET, exactly one refresh-and-retry on 401. Same bound and the same
    reason as the WHOOP client's."""
    token = await oauth_store.access_token(PROVIDER, member_sub, _refresh)
    async with httpx.AsyncClient(timeout=15.0) as client:
        for attempt in (1, 2):
            response = await client.get(
                f"{BASE_URL}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 401 and attempt == 1:
                token = await oauth_store.refresh_now(
                    PROVIDER, member_sub, _refresh
                )
                continue
            response.raise_for_status()
            return response.json()
    raise AssertionError("unreachable")


def _window(days: int) -> dict:
    """Oura takes local date strings. One extra day at each end because
    eve-tools does not know the member's timezone and "today" here is a UTC
    date - `_newest_first` trims on Oura's own `day` attribution afterwards.
    """
    today = datetime.now(UTC).date()
    return {
        "start_date": (today - timedelta(days=days + 1)).isoformat(),
        "end_date": (today + timedelta(days=1)).isoformat(),
    }


# Spec 4.4: one page is enough at days <= 14. Oura's date-bounded collections
# return the whole window in one response at this size, and neither client
# implements next_token paging - raise this, or add paging, if the window
# ever grows.
_PAGE_LIMIT = 25


def _num(value: object) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _hours(seconds: object) -> float | None:
    """Oura reports durations in SECONDS (WHOOP uses milliseconds)."""
    value = _num(seconds)
    return None if value is None else round(value / 3600, 2)


def _newest_first(entries: list[dict], days: int) -> list[dict]:
    by_date: dict[str, dict] = {}
    for entry in entries:
        by_date.setdefault(entry["date"], entry)
    return [by_date[d] for d in sorted(by_date, reverse=True)][:days]


async def _sleep_by_date(member_sub: str, days: int) -> dict[str, dict]:
    """The night's sleep per local day, from the detailed collection.

    Oura's `sleep` collection holds every sleep period, naps included. The
    longest one for a day is the night - taking the first would let a
    20-minute doze supply the day's HRV and resting heart rate.
    """
    raw = await _get(member_sub, "/sleep", _window(days))
    best: dict[str, dict] = {}
    for record in raw.get("data") or []:
        if not isinstance(record, dict):
            logger.warning("Oura sleep record was not a dict: %r", record)
            continue
        day = record.get("day")
        if not day:
            continue
        current = best.get(day)
        if current is None or (_num(record.get("total_sleep_duration")) or 0) > (
            _num(current.get("total_sleep_duration")) or 0
        ):
            best[day] = record
    return best


async def get_recovery(member_sub: str, days: int) -> list[dict]:
    sleep = await _sleep_by_date(member_sub, days)
    raw = await _get(member_sub, "/daily_readiness", _window(days))
    entries = []
    for record in raw.get("data") or []:
        if not isinstance(record, dict):
            logger.warning("Oura readiness record was not a dict: %r", record)
            continue
        day = record.get("day")
        if not day:
            continue
        night = sleep.get(day) or {}
        entries.append({
            "date": day,
            "source": PROVIDER,
            "score_0_100": _num(record.get("score")),
            # From the sleep collection, NOT contributors.hrv_balance - that
            # is a 0-100 rating, not milliseconds.
            "hrv_ms": _num(night.get("average_hrv")),
            "resting_hr": _num(night.get("lowest_heart_rate")),
            "temp_deviation_c": _num(record.get("temperature_deviation")),
        })
    return _newest_first(entries, days)
