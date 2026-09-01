"""tests/test_eve_tools_immich.py"""
import base64

import httpx
import pytest

from eve_tools import immich


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_IMMICH_URL", "http://immich.test")
    monkeypatch.setenv("EVE_TOOLS_IMMICH_API_KEY", "immich-key")


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_album_assets_returns_ids_and_filenames(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(
            200,
            json={
                "id": "album-1",
                "albumName": "Wardrobe",
                "assets": [
                    {"id": "asset-1", "originalFileName": "blazer.jpg"},
                    {"id": "asset-2", "originalFileName": "boots.jpg"},
                ],
            },
        )

    monkeypatch.setattr(immich, "_transport_for_test", _transport(handler))

    result = await immich.album_assets("album-1")

    assert result == {
        "assets": [
            {"id": "asset-1", "filename": "blazer.jpg"},
            {"id": "asset-2", "filename": "boots.jpg"},
        ]
    }
    assert seen["url"] == "http://immich.test/api/albums/album-1"
    assert seen["key"] == "immich-key"


async def test_album_assets_is_capped(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "assets": [
                    {"id": f"asset-{n}", "originalFileName": f"{n}.jpg"}
                    for n in range(immich._MAX_ASSETS + 25)
                ]
            },
        )

    monkeypatch.setattr(immich, "_transport_for_test", _transport(handler))

    result = await immich.album_assets("album-1")

    assert len(result["assets"]) == immich._MAX_ASSETS


async def test_asset_image_returns_base64_and_content_type(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(
            200, content=b"\xff\xd8jpegbytes", headers={"content-type": "image/jpeg"}
        )

    monkeypatch.setattr(immich, "_transport_for_test", _transport(handler))

    result = await immich.asset_image("asset-1")

    assert result["asset_id"] == "asset-1"
    assert result["content_type"] == "image/jpeg"
    assert base64.b64decode(result["base64"]) == b"\xff\xd8jpegbytes"
    assert seen["url"] == (
        "http://immich.test/api/assets/asset-1/thumbnail?size=preview"
    )
