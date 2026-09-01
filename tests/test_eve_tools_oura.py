"""Oura v2 client and normalizers.

The join in `get_recovery` is the thing to keep an eye on: daily_readiness
carries only CONTRIBUTOR scores (0-100 sub-scores), not raw HRV or resting
heart rate. Those live in the detailed `sleep` collection, so recovery is two
requests where WHOOP needs one.
"""

from __future__ import annotations

import httpx
import pytest
import respx

BASE = "https://api.ouraring.com/v2/usercollection"
TOKEN_URL = "https://api.ouraring.com/oauth/token"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    async def _access_token(provider, member_sub, refresh):
        assert provider == "oura"
        return "acc-1"

    monkeypatch.setattr("eve_tools.oura.oauth_store.access_token", _access_token)


def _sleep_record(day="2026-09-01", **overrides):
    return {
        "id": "sleep-1",
        "day": day,
        "type": "long_sleep",
        "total_sleep_duration": 26_640,
        "deep_sleep_duration": 4_320,
        "rem_sleep_duration": 6_480,
        "efficiency": 92,
        "average_hrv": 61,
        "lowest_heart_rate": 48,
        **overrides,
    }


@respx.mock
async def test_recovery_joins_readiness_with_the_sleep_collection():
    respx.get(f"{BASE}/daily_readiness").mock(
        return_value=httpx.Response(200, json={"data": [{
            "id": "r-1", "day": "2026-09-01", "score": 81,
            "temperature_deviation": -0.2,
            "contributors": {"hrv_balance": 90, "resting_heart_rate": 95},
        }]})
    )
    respx.get(f"{BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": [_sleep_record()]})
    )
    from eve_tools import oura

    result = await oura.get_recovery("sub-noah", days=1)
    assert result == [{
        "date": "2026-09-01",
        "source": "oura",
        "score_0_100": 81,
        "hrv_ms": 61,
        "resting_hr": 48,
        "temp_deviation_c": -0.2,
    }]


@respx.mock
async def test_readiness_without_a_matching_sleep_row_nulls_the_raw_fields():
    respx.get(f"{BASE}/daily_readiness").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "r-1", "day": "2026-09-01", "score": 81},
        ]})
    )
    respx.get(f"{BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    from eve_tools import oura

    result = await oura.get_recovery("sub-noah", days=1)
    assert result[0]["hrv_ms"] is None
    assert result[0]["resting_hr"] is None
    assert result[0]["score_0_100"] == 81


@respx.mock
async def test_the_date_is_ouras_own_local_day_string():
    respx.get(f"{BASE}/daily_readiness").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "r-1", "day": "2026-08-30", "score": 70},
        ]})
    )
    respx.get(f"{BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    from eve_tools import oura

    assert (await oura.get_recovery("sub-noah", days=3))[0]["date"] == "2026-08-30"


@respx.mock
async def test_a_nap_does_not_win_the_join_over_the_nights_sleep():
    respx.get(f"{BASE}/daily_readiness").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "r-1", "day": "2026-09-01", "score": 81},
        ]})
    )
    respx.get(f"{BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": [
            _sleep_record(type="late_nap", total_sleep_duration=1_800,
                          average_hrv=40, lowest_heart_rate=60),
            _sleep_record(),
        ]})
    )
    from eve_tools import oura

    result = await oura.get_recovery("sub-noah", days=1)
    assert result[0]["hrv_ms"] == 61
    assert result[0]["resting_hr"] == 48


@respx.mock
async def test_results_are_newest_first_and_trimmed_to_days():
    respx.get(f"{BASE}/daily_readiness").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "a", "day": "2026-08-30", "score": 60},
            {"id": "b", "day": "2026-08-31", "score": 70},
            {"id": "c", "day": "2026-09-01", "score": 80},
        ]})
    )
    respx.get(f"{BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    from eve_tools import oura

    result = await oura.get_recovery("sub-noah", days=2)
    assert [r["date"] for r in result] == ["2026-09-01", "2026-08-31"]


@respx.mock
async def test_a_401_refreshes_once_and_retries(monkeypatch):
    async def _refresh_now(provider, member_sub, refresh):
        return "acc-2"

    monkeypatch.setattr("eve_tools.oura.oauth_store.refresh_now", _refresh_now)
    seen = []

    def _handler(request):
        seen.append(request.headers["Authorization"])
        if len(seen) == 1:
            return httpx.Response(401, json={"detail": "unauthorized"})
        return httpx.Response(200, json={"data": []})

    respx.get(f"{BASE}/daily_readiness").mock(side_effect=_handler)
    respx.get(f"{BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    from eve_tools import oura

    assert await oura.get_recovery("sub-noah", days=1) == []
    assert seen == ["Bearer acc-1", "Bearer acc-2"]


@respx.mock
async def test_refresh_posts_the_form_oura_documents():
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "acc-2", "refresh_token": "ref-2",
            "expires_in": 86400, "token_type": "bearer",
        })
    )
    from eve_tools import oura

    result = await oura._refresh("ref-1")
    assert result["access_token"] == "acc-2"
    body = dict(pair.split("=") for pair in route.calls[0].request.content.decode().split("&"))
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "ref-1"


@respx.mock
async def test_sleep_joins_the_daily_score_with_the_detailed_durations():
    """daily_sleep carries the score; the `sleep` collection carries the
    durations. Neither has both."""
    respx.get(f"{BASE}/daily_sleep").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "ds-1", "day": "2026-09-01", "score": 81},
        ]})
    )
    respx.get(f"{BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": [_sleep_record()]})
    )
    from eve_tools import oura

    result = await oura.get_sleep("sub-noah", days=1)
    assert result == [{
        "date": "2026-09-01",
        "source": "oura",
        "score_0_100": 81,
        "hours": 7.4,
        "deep_hours": 1.2,
        "rem_hours": 1.8,
        "efficiency_pct": 92,
        "hrv_ms": 61,
        "resting_hr": 48,
    }]


@respx.mock
async def test_a_daily_score_with_no_detailed_row_nulls_the_durations():
    respx.get(f"{BASE}/daily_sleep").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "ds-1", "day": "2026-09-01", "score": 81},
        ]})
    )
    respx.get(f"{BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    from eve_tools import oura

    result = await oura.get_sleep("sub-noah", days=1)
    assert result[0]["hours"] is None, "zero hours would read as 'you did not sleep'"
    assert result[0]["score_0_100"] == 81


@respx.mock
async def test_activity_maps_score_calories_and_steps():
    respx.get(f"{BASE}/daily_activity").mock(
        return_value=httpx.Response(200, json={"data": [{
            "id": "da-1", "day": "2026-09-01", "score": 88,
            "active_calories": 612, "steps": 11_284,
            "target_calories": 500,
        }]})
    )
    from eve_tools import oura

    result = await oura.get_activity("sub-noah", days=1)
    assert result == [{
        "date": "2026-09-01",
        "source": "oura",
        "score_0_100": 88,
        # Oura has no strain metric at all. None, not zero - a 0 here reads
        # as "you did nothing strenuous", which is a claim. Spec 4.1.
        "strain_0_21": None,
        "active_calories": 612,
        "steps": 11_284,
        # daily_activity has no per-workout breakdown. Empty list, because
        # workouts is a list field. Spec 4.2.
        "workouts": [],
    }]


@respx.mock
async def test_a_missing_step_count_is_none_not_zero():
    respx.get(f"{BASE}/daily_activity").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "da-1", "day": "2026-09-01", "score": 88},
        ]})
    )
    from eve_tools import oura

    result = await oura.get_activity("sub-noah", days=1)
    assert result[0]["steps"] is None
    assert result[0]["active_calories"] is None
