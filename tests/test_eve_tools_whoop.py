"""WHOOP v2 client and normalizers.

Every test fakes HTTP with respx and the token store with monkeypatch: this
tier must never touch api.prod.whoop.com.
"""

from __future__ import annotations

import httpx
import pytest
import respx

BASE = "https://api.prod.whoop.com/developer"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    """Every client call starts by asking the store for an access token."""
    async def _access_token(provider, member_sub, refresh):
        assert provider == "whoop"
        return "acc-1"

    monkeypatch.setattr("eve_tools.whoop.oauth_store.access_token", _access_token)


def _recovery_record(score_state="SCORED", **score):
    return {
        "cycle_id": 93845,
        "sleep_id": "ec3c2f0e-0000-4000-8000-000000000000",
        "created_at": "2026-09-01T14:02:00.000Z",
        "score_state": score_state,
        "score": {
            "recovery_score": 68,
            "resting_heart_rate": 51,
            "hrv_rmssd_milli": 84.2,
            "skin_temp_celsius": 33.1,
            **score,
        },
    }


@respx.mock
async def test_recovery_maps_whoops_field_names_onto_the_normalized_shape():
    respx.get(f"{BASE}/v2/recovery").mock(
        return_value=httpx.Response(200, json={"records": [_recovery_record()]})
    )
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": [
            {"id": 93845, "start": "2026-09-01T07:30:00.000Z",
             "timezone_offset": "-07:00", "score_state": "SCORED",
             "score": {"strain": 14.2}},
        ]})
    )
    from eve_tools import whoop

    result = await whoop.get_recovery("sub-noah", days=1)
    assert result == [{
        "date": "2026-09-01",
        "source": "whoop",
        "score_0_100": 68,
        "hrv_ms": 84.2,
        "resting_hr": 51,
        "temp_deviation_c": None,
    }]


@respx.mock
async def test_an_unscored_record_yields_nulls_rather_than_raising():
    record = {
        "cycle_id": 93845,
        "created_at": "2026-09-01T14:02:00.000Z",
        "score_state": "PENDING_SCORE",
    }
    respx.get(f"{BASE}/v2/recovery").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": [
            {"id": 93845, "start": "2026-09-01T07:30:00.000Z",
             "timezone_offset": "-07:00"},
        ]})
    )
    from eve_tools import whoop

    result = await whoop.get_recovery("sub-noah", days=1)
    assert result == [{
        "date": "2026-09-01", "source": "whoop", "score_0_100": None,
        "hrv_ms": None, "resting_hr": None, "temp_deviation_c": None,
    }]


@respx.mock
async def test_no_recovery_yet_this_morning_is_an_empty_list_not_an_error():
    respx.get(f"{BASE}/v2/recovery").mock(
        return_value=httpx.Response(200, json={"records": []})
    )
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": []})
    )
    from eve_tools import whoop

    assert await whoop.get_recovery("sub-noah", days=1) == []


@respx.mock
async def test_the_date_comes_from_the_records_own_timezone_offset():
    respx.get(f"{BASE}/v2/recovery").mock(
        return_value=httpx.Response(200, json={"records": [
            {**_recovery_record(), "cycle_id": 111},
        ]})
    )
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": [
            {"id": 111, "start": "2026-09-02T05:30:00.000Z",
             "timezone_offset": "-07:00"},
        ]})
    )
    from eve_tools import whoop

    result = await whoop.get_recovery("sub-noah", days=1)
    assert result[0]["date"] == "2026-09-01"


@pytest.mark.parametrize("offset", [None, "not-an-offset"])
@respx.mock
async def test_recovery_without_a_valid_provider_timezone_offset_is_dropped(offset):
    """A UTC calendar date is not an acceptable substitute for provider time."""
    respx.get(f"{BASE}/v2/recovery").mock(
        return_value=httpx.Response(200, json={"records": [
            {**_recovery_record(), "cycle_id": 111},
        ]})
    )
    cycle = {"id": 111, "start": "2026-09-02T05:30:00.000Z"}
    if offset is not None:
        cycle["timezone_offset"] = offset
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": [cycle]})
    )
    from eve_tools import whoop

    assert await whoop.get_recovery("sub-noah", days=1) == []


@respx.mock
async def test_results_are_newest_first_and_trimmed_to_days():
    respx.get(f"{BASE}/v2/recovery").mock(
        return_value=httpx.Response(200, json={"records": [
            {**_recovery_record(), "cycle_id": 1, "score": {"recovery_score": 60}},
            {**_recovery_record(), "cycle_id": 2, "score": {"recovery_score": 70}},
            {**_recovery_record(), "cycle_id": 3, "score": {"recovery_score": 80}},
        ]})
    )
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": [
            {"id": 1, "start": "2026-08-30T15:00:00.000Z", "timezone_offset": "-07:00"},
            {"id": 2, "start": "2026-08-31T15:00:00.000Z", "timezone_offset": "-07:00"},
            {"id": 3, "start": "2026-09-01T15:00:00.000Z", "timezone_offset": "-07:00"},
        ]})
    )
    from eve_tools import whoop

    result = await whoop.get_recovery("sub-noah", days=2)
    assert [r["date"] for r in result] == ["2026-09-01", "2026-08-31"]


@respx.mock
async def test_a_401_refreshes_once_and_retries(monkeypatch):
    calls = []

    async def _refresh_now(provider, member_sub, refresh):
        calls.append(provider)
        return "acc-2"

    monkeypatch.setattr("eve_tools.whoop.oauth_store.refresh_now", _refresh_now)

    seen = []

    def _handler(request):
        seen.append(request.headers["Authorization"])
        if len(seen) == 1:
            return httpx.Response(401, json={"error": "unauthorized"})
        return httpx.Response(200, json={"records": []})

    respx.get(f"{BASE}/v2/recovery").mock(side_effect=_handler)
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": []})
    )
    from eve_tools import whoop

    assert await whoop.get_recovery("sub-noah", days=1) == []
    assert calls == ["whoop"]
    assert seen == ["Bearer acc-1", "Bearer acc-2"]


@respx.mock
async def test_a_second_401_is_raised_rather_than_retried_forever(monkeypatch):
    async def _refresh_now(provider, member_sub, refresh):
        return "acc-2"

    monkeypatch.setattr("eve_tools.whoop.oauth_store.refresh_now", _refresh_now)
    route = respx.get(f"{BASE}/v2/recovery").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": []})
    )
    from eve_tools import whoop

    with pytest.raises(httpx.HTTPStatusError):
        await whoop.get_recovery("sub-noah", days=1)
    assert route.call_count == 2


@respx.mock
async def test_refresh_posts_the_form_whoop_documents():
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "acc-2", "refresh_token": "ref-2",
            "expires_in": 3600, "scope": "read:recovery", "token_type": "bearer",
        })
    )
    from eve_tools import whoop

    result = await whoop._refresh("ref-1")
    assert result["access_token"] == "acc-2"
    assert result["refresh_token"] == "ref-2"
    body = dict(pair.split("=") for pair in route.calls[0].request.content.decode().split("&"))
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "ref-1"


@respx.mock
async def test_sleep_converts_stage_milliseconds_to_hours():
    respx.get(f"{BASE}/v2/activity/sleep").mock(
        return_value=httpx.Response(200, json={"records": [{
            "id": "aaaa0000-0000-4000-8000-000000000000",
            "start": "2026-09-01T06:10:00.000Z",
            "timezone_offset": "-07:00",
            "nap": False,
            "score_state": "SCORED",
            "score": {
                "stage_summary": {
                    "total_in_bed_time_milli": 28_800_000,
                    "total_awake_time_milli": 1_800_000,
                    "total_slow_wave_sleep_time_milli": 5_400_000,
                    "total_rem_sleep_time_milli": 7_200_000,
                },
                "sleep_performance_percentage": 88,
                "sleep_efficiency_percentage": 93.5,
            },
        }]})
    )
    from eve_tools import whoop

    result = await whoop.get_sleep("sub-noah", days=1)
    assert result == [{
        "date": "2026-08-31",
        "source": "whoop",
        "score_0_100": 88,
        "hours": 7.5,
        "deep_hours": 1.5,
        "rem_hours": 2.0,
        "efficiency_pct": 93.5,
        "hrv_ms": None,
        "resting_hr": None,
    }]


@respx.mock
async def test_a_nap_does_not_displace_the_nights_sleep():
    respx.get(f"{BASE}/v2/activity/sleep").mock(
        return_value=httpx.Response(200, json={"records": [
            {"id": "n", "start": "2026-09-01T21:00:00.000Z",
             "timezone_offset": "-07:00", "nap": True, "score_state": "SCORED",
             "score": {"stage_summary": {"total_in_bed_time_milli": 1_800_000,
                                         "total_awake_time_milli": 0},
                       "sleep_performance_percentage": 20}},
            {"id": "m", "start": "2026-09-01T14:00:00.000Z",
             "timezone_offset": "-07:00", "nap": False, "score_state": "SCORED",
             "score": {"stage_summary": {"total_in_bed_time_milli": 28_800_000,
                                         "total_awake_time_milli": 1_800_000},
                       "sleep_performance_percentage": 88}},
        ]})
    )
    from eve_tools import whoop

    result = await whoop.get_sleep("sub-noah", days=1)
    assert len(result) == 1
    assert result[0]["score_0_100"] == 88


@respx.mock
async def test_unscored_sleep_yields_nulls_not_zero_hours():
    respx.get(f"{BASE}/v2/activity/sleep").mock(
        return_value=httpx.Response(200, json={"records": [{
            "id": "a", "start": "2026-09-01T06:10:00.000Z",
            "timezone_offset": "-07:00", "nap": False,
            "score_state": "PENDING_SCORE",
        }]})
    )
    from eve_tools import whoop

    result = await whoop.get_sleep("sub-noah", days=1)
    assert result[0]["hours"] is None
    assert result[0]["deep_hours"] is None


@respx.mock
async def test_activity_maps_strain_and_converts_kilojoules_to_calories():
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": [{
            "id": 93845, "start": "2026-09-01T14:00:00.000Z",
            "timezone_offset": "-07:00", "score_state": "SCORED",
            "score": {"strain": 14.2, "kilojoule": 3397.0,
                      "average_heart_rate": 78, "max_heart_rate": 171},
        }]})
    )
    respx.get(f"{BASE}/v2/activity/workout").mock(
        return_value=httpx.Response(200, json={"records": [{
            "id": "w", "start": "2026-09-01T16:00:00.000Z",
            "end": "2026-09-01T17:02:00.000Z", "timezone_offset": "-07:00",
            "sport_name": "cycling", "score_state": "SCORED",
            "score": {"average_heart_rate": 138, "strain": 9.1},
        }]})
    )
    from eve_tools import whoop

    result = await whoop.get_activity("sub-noah", days=1)
    assert result == [{
        "date": "2026-09-01",
        "source": "whoop",
        "score_0_100": None,
        "strain_0_21": 14.2,
        "active_calories": 812,
        "steps": None,
        "workouts": [{"sport": "cycling", "duration_min": 62, "avg_hr": 138}],
    }]


@respx.mock
async def test_a_day_with_no_workouts_gets_an_empty_list_not_none():
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": [{
            "id": 1, "start": "2026-09-01T14:00:00.000Z",
            "timezone_offset": "-07:00", "score_state": "SCORED",
            "score": {"strain": 4.1, "kilojoule": 1000.0},
        }]})
    )
    respx.get(f"{BASE}/v2/activity/workout").mock(
        return_value=httpx.Response(200, json={"records": []})
    )
    from eve_tools import whoop

    result = await whoop.get_activity("sub-noah", days=1)
    assert result[0]["workouts"] == []
