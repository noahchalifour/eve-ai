from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from eve_tools.app import app


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_API_KEY", "test-key")


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_invoke_requires_the_bearer_token():
    async with await _client() as client:
        response = await client.post("/invoke", json={"tool": "home.get_state", "arguments": {}})
    assert response.status_code == 401


async def test_invoke_dispatches_to_the_registered_handler(monkeypatch):
    mock_get_state = AsyncMock(return_value={"state": "on"})
    monkeypatch.setattr("eve_tools.app.home_assistant.get_state", mock_get_state)
    async with await _client() as client:
        response = await client.post(
            "/invoke",
            json={"tool": "home.get_state", "arguments": {"entity_id": "light.kitchen"}},
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 200
    assert response.json() == {"result": {"state": "on"}}
    mock_get_state.assert_awaited_once_with("light.kitchen")


async def test_invoke_dispatches_immich_album_assets(monkeypatch):
    mock_album = AsyncMock(return_value={"assets": []})
    monkeypatch.setattr("eve_tools.app.immich.album_assets", mock_album)
    async with await _client() as client:
        response = await client.post(
            "/invoke",
            json={"tool": "immich.album_assets", "arguments": {"album_id": "album-1"}},
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 200
    assert response.json() == {"result": {"assets": []}}
    mock_album.assert_awaited_once_with("album-1")


async def test_invoke_dispatches_immich_asset_image(monkeypatch):
    mock_image = AsyncMock(
        return_value={"asset_id": "asset-1", "content_type": "image/jpeg", "base64": "aW1hZ2U="}
    )
    monkeypatch.setattr("eve_tools.app.immich.asset_image", mock_image)
    async with await _client() as client:
        response = await client.post(
            "/invoke",
            json={"tool": "immich.asset_image", "arguments": {"asset_id": "asset-1"}},
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 200
    assert response.json() == {
        "result": {"asset_id": "asset-1", "content_type": "image/jpeg", "base64": "aW1hZ2U="}
    }
    mock_image.assert_awaited_once_with("asset-1")


async def test_invoke_returns_404_for_an_unknown_tool():
    async with await _client() as client:
        response = await client.post(
            "/invoke",
            json={"tool": "nonexistent.tool", "arguments": {}},
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 404


async def test_invoke_turns_an_upstream_exception_into_an_error_body(monkeypatch):
    async def _boom(_entity_id):
        raise RuntimeError("Home Assistant unreachable")

    monkeypatch.setattr("eve_tools.app.home_assistant.get_state", _boom)
    async with await _client() as client:
        response = await client.post(
            "/invoke",
            json={"tool": "home.get_state", "arguments": {"entity_id": "light.kitchen"}},
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 200
    assert "Home Assistant unreachable" in response.json()["error"]


async def test_healthz_needs_no_auth():
    async with await _client() as client:
        response = await client.get("/healthz")
    assert response.status_code == 200


def test_home_weather_is_a_dispatchable_tool():
    """The dispatch table is the whole routing layer - a handler that exists
    but is unregistered 404s at runtime with nothing failing at import."""
    from eve_tools.app import _HANDLERS

    assert "home.weather" in _HANDLERS


def test_the_health_tools_are_dispatchable():
    """The dispatch table is the whole routing layer - a handler that exists
    but is unregistered 404s at runtime with nothing failing at import."""
    from eve_tools.app import _HANDLERS

    assert "health.get_recovery" in _HANDLERS
    assert "health.get_sleep" in _HANDLERS
    assert "health.get_activity" in _HANDLERS


async def test_health_get_recovery_dispatches_with_member_and_days(monkeypatch):
    mock_get = AsyncMock(return_value={"recovery": []})
    monkeypatch.setattr("eve_tools.app.health.get_recovery", mock_get)
    async with await _client() as client:
        response = await client.post(
            "/invoke",
            json={"tool": "health.get_recovery",
                  "arguments": {"member_sub": "sub-noah", "days": 3}},
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 200
    mock_get.assert_awaited_once_with("sub-noah", 3)


async def test_health_get_recovery_defaults_days_to_one(monkeypatch):
    mock_get = AsyncMock(return_value={"recovery": []})
    monkeypatch.setattr("eve_tools.app.health.get_recovery", mock_get)
    async with await _client() as client:
        await client.post(
            "/invoke",
            json={"tool": "health.get_recovery", "arguments": {"member_sub": "sub-noah"}},
            headers={"Authorization": "Bearer test-key"},
        )
    mock_get.assert_awaited_once_with("sub-noah", 1)
