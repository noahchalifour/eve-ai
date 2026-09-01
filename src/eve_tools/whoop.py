"""WHOOP v2 client and recovery normalizers.

Recovery may be unscored, and it does not exist until the night's sleep cycle
closes. Both are normal provider states, not client errors. This module
returns bare lists; ``health.py`` owns the provider-merge envelope.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx

from eve_tools import oauth_store
from eve_tools.settings import get_tools_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.prod.whoop.com/developer"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
PROVIDER = "whoop"
_PAGE_LIMIT = 25


async def _refresh(refresh_token: str) -> dict:
    """Exchange a WHOOP refresh token and return the provider response."""
    settings = get_tools_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.whoop_client_id,
                "client_secret": settings.whoop_client_secret,
                "scope": "offline",
            },
        )
        response.raise_for_status()
        return response.json()


async def _get(member_sub: str, path: str, params: dict) -> dict:
    """GET once, refreshing and retrying exactly once after a 401."""
    token = await oauth_store.access_token(PROVIDER, member_sub, _refresh)
    async with httpx.AsyncClient(timeout=15.0) as client:
        for attempt in (1, 2):
            response = await client.get(
                f"{BASE_URL}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 401 and attempt == 1:
                token = await oauth_store.refresh_now(PROVIDER, member_sub, _refresh)
                continue
            response.raise_for_status()
            return response.json()
    raise AssertionError("unreachable")


def _window(days: int) -> dict:
    """Over-fetch UTC instants; trim by the provider-attributed local date."""
    now = datetime.now(UTC)
    return {
        "start": (now - timedelta(days=days + 1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": _PAGE_LIMIT,
    }


def _score(record: dict) -> dict:
    score = record.get("score")
    return score if isinstance(score, dict) else {}


def _num(value: object) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _record_date(record: dict) -> str | None:
    """Return this record's local date using WHOOP's timezone offset."""
    raw = record.get("start") or record.get("created_at")
    if not raw:
        return None
    try:
        instant = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        logger.warning("unparseable WHOOP timestamp, dropping record: %r", raw)
        return None
    offset = record.get("timezone_offset")
    if not isinstance(offset, str):
        logger.warning("missing WHOOP timezone_offset, dropping record")
        return None
    try:
        if (
            len(offset) != 6
            or offset[0] not in "+-"
            or offset[3] != ":"
            or not offset[1:3].isdigit()
            or not offset[4:].isdigit()
        ):
            raise ValueError
        hours = int(offset[1:3])
        minutes = int(offset[4:])
        if hours > 14 or minutes > 59:
            raise ValueError
        sign = -1 if offset.startswith("-") else 1
        instant = instant + sign * timedelta(hours=hours, minutes=minutes)
    except ValueError:
        logger.warning("unparseable WHOOP timezone_offset, dropping record: %r", offset)
        return None
    return instant.date().isoformat()


def _newest_first(entries: list[dict], days: int) -> list[dict]:
    by_date: dict[str, dict] = {}
    for entry in entries:
        by_date.setdefault(entry["date"], entry)
    return [by_date[date] for date in sorted(by_date, reverse=True)][:days]


async def _cycle_dates(member_sub: str, days: int) -> dict[int, str]:
    """Map WHOOP cycle IDs to provider-attributed local dates."""
    raw = await _get(member_sub, "/v2/cycle", _window(days))
    dates = {}
    for record in raw.get("records") or []:
        if not isinstance(record, dict):
            continue
        date = _record_date(record)
        if record.get("id") is not None and date:
            dates[record["id"]] = date
    return dates


async def get_recovery(member_sub: str, days: int) -> list[dict]:
    """Return normalized WHOOP recovery entries, newest first."""
    cycles = await _cycle_dates(member_sub, days)
    raw = await _get(member_sub, "/v2/recovery", _window(days))
    entries = []
    for record in raw.get("records") or []:
        if not isinstance(record, dict):
            logger.warning("WHOOP recovery record was not a dict: %r", record)
            continue
        date = cycles.get(record.get("cycle_id")) or _record_date(record)
        if not date:
            continue
        score = _score(record)
        entries.append({
            "date": date,
            "source": PROVIDER,
            "score_0_100": _num(score.get("recovery_score")),
            "hrv_ms": _num(score.get("hrv_rmssd_milli")),
            "resting_hr": _num(score.get("resting_heart_rate")),
            "temp_deviation_c": None,
        })
    return _newest_first(entries, days)


def _hours(millis: object) -> float | None:
    value = _num(millis)
    return None if value is None else round(value / 3_600_000, 2)


async def get_sleep(member_sub: str, days: int) -> list[dict]:
    raw = await _get(member_sub, "/v2/activity/sleep", _window(days))
    entries = []
    for record in raw.get("records") or []:
        if not isinstance(record, dict):
            logger.warning("WHOOP sleep record was not a dict: %r", record)
            continue
        if record.get("nap"):
            continue
        date = _record_date(record)
        if not date:
            continue
        score = _score(record)
        stages = score.get("stage_summary")
        stages = stages if isinstance(stages, dict) else {}
        in_bed = _num(stages.get("total_in_bed_time_milli"))
        awake = _num(stages.get("total_awake_time_milli"))
        asleep = None if in_bed is None else in_bed - (awake or 0)
        entries.append({
            "date": date,
            "source": PROVIDER,
            "score_0_100": _num(score.get("sleep_performance_percentage")),
            "hours": _hours(asleep),
            "deep_hours": _hours(stages.get("total_slow_wave_sleep_time_milli")),
            "rem_hours": _hours(stages.get("total_rem_sleep_time_milli")),
            "efficiency_pct": _num(score.get("sleep_efficiency_percentage")),
            "hrv_ms": None,
            "resting_hr": None,
        })
    return _newest_first(entries, days)


def _workouts_by_date(records: list) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for record in records or []:
        if not isinstance(record, dict):
            continue
        date = _record_date(record)
        if not date:
            continue
        start, end = record.get("start"), record.get("end")
        duration = None
        if start and end:
            try:
                duration = round(
                    (
                        datetime.fromisoformat(str(end).replace("Z", "+00:00"))
                        - datetime.fromisoformat(str(start).replace("Z", "+00:00"))
                    ).total_seconds()
                    / 60
                )
            except ValueError:
                logger.warning("unparseable WHOOP workout bounds: %r %r", start, end)
        grouped.setdefault(date, []).append({
            "sport": record.get("sport_name"),
            "duration_min": duration,
            "avg_hr": _num(_score(record).get("average_heart_rate")),
        })
    return grouped


async def get_activity(member_sub: str, days: int) -> list[dict]:
    raw = await _get(member_sub, "/v2/cycle", _window(days))
    workouts = _workouts_by_date(
        (await _get(member_sub, "/v2/activity/workout", _window(days))).get("records")
    )
    entries = []
    for record in raw.get("records") or []:
        if not isinstance(record, dict):
            logger.warning("WHOOP cycle record was not a dict: %r", record)
            continue
        date = _record_date(record)
        if not date:
            continue
        score = _score(record)
        kilojoules = _num(score.get("kilojoule"))
        entries.append({
            "date": date,
            "source": PROVIDER,
            "score_0_100": None,
            "strain_0_21": _num(score.get("strain")),
            "active_calories": (
                None if kilojoules is None else round(kilojoules / 4.184)
            ),
            "steps": None,
            "workouts": workouts.get(date, []),
        })
    return _newest_first(entries, days)
