"""tests/test_images_app.py - Authentication is Aegra's; ownership is ours.
The store is faked: its SQL is covered by test_images_store.py."""
from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from eve.images.store import ImageRow

IMAGE_ID = "3f2a9c01-0000-4000-8000-000000000001"


def _jpeg_bytes(size=(50, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (1, 2, 3)).save(buf, "JPEG")
    return buf.getvalue()


def _row(**overrides) -> ImageRow:
    now = datetime.now(UTC)
    fields = dict(
        id=IMAGE_ID, member_sub="sub-noah", thread_id="t1", origin="upload",
        source_ref=None, content_type="image/jpeg", bytes=b"JPEGDATA",
        width=50, height=40, caption=None, created_at=now,
        expires_at=now + timedelta(days=30),
    )
    return ImageRow(**{**fields, **overrides})


@pytest.fixture
def client(monkeypatch):
    from eve import http_app
    from eve.images import app as images_app

    calls: dict = {}

    async def fake_put(member_sub, image, *, origin, thread_id=None, source_ref=None, now=None):
        calls["put"] = (member_sub, origin, thread_id, image.width, image.height)
        return _row(width=image.width, height=image.height, thread_id=thread_id)

    async def fake_get(image_id, member_sub, *, include_expired=False, now=None):
        calls["get"] = (image_id, member_sub, include_expired)
        return calls.get("row")

    monkeypatch.setattr(images_app.store, "put", fake_put)
    monkeypatch.setattr(images_app.store, "get", fake_get)
    http_app.app.dependency_overrides[images_app.require_auth] = lambda: None
    http_app.app.dependency_overrides[images_app.current_member] = (
        lambda: {"sub": "sub-noah", "permissions": []}
    )
    test_client = TestClient(http_app.app)
    test_client.calls = calls
    yield test_client
    http_app.app.dependency_overrides.clear()


def test_an_upload_is_normalised_stored_and_answered_with_its_id(client):
    response = client.post(
        "/images",
        files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")},
        data={"thread_id": "t1"},
    )
    assert response.status_code == 200
    assert response.json() == {"image_id": IMAGE_ID, "width": 50, "height": 40}
    assert client.calls["put"] == ("sub-noah", "upload", "t1", 50, 40)


def test_a_non_image_is_415(client):
    response = client.post(
        "/images", files={"file": ("x.pdf", b"%PDF-1.7", "application/pdf")}
    )
    assert response.status_code == 415
    assert "put" not in client.calls


def test_an_oversized_upload_is_413_before_decoding(client, monkeypatch):
    from eve.settings import get_settings

    monkeypatch.setattr(get_settings(), "image_max_upload_bytes", 10)
    response = client.post(
        "/images", files={"file": ("p.jpg", _jpeg_bytes(), "image/jpeg")}
    )
    assert response.status_code == 413


def test_the_owner_gets_the_bytes_with_a_private_cache_header(client):
    client.calls["row"] = _row()
    response = client.get(f"/images/{IMAGE_ID}")
    assert response.status_code == 200
    assert response.content == b"JPEGDATA"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert client.calls["get"] == (IMAGE_ID, "sub-noah", True)


def test_missing_or_foreign_is_404(client):
    client.calls["row"] = None
    assert client.get(f"/images/{IMAGE_ID}").status_code == 404


def test_expired_is_410(client):
    client.calls["row"] = _row(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert client.get(f"/images/{IMAGE_ID}").status_code == 410
