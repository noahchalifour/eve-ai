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
