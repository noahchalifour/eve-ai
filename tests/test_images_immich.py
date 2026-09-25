"""tests/test_images_immich.py - the Immich cache fill. Immich's key stays in
eve-tools; the phone never learns an Immich URL (spec 3.4)."""
from __future__ import annotations

import base64
import io
import json
from datetime import UTC, datetime, timedelta

from PIL import Image

from eve.images import immich
from eve.images.store import ImageRow


def _preview() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (300, 400), (0, 0, 90)).save(buf, "JPEG")
    return json.dumps({"asset_id": "a-1", "content_type": "image/jpeg",
                       "base64": base64.b64encode(buf.getvalue()).decode()})


async def test_an_asset_is_cached_and_its_id_returned(monkeypatch):
    stored = {}

    async def fake_invoke(tool, arguments, timeout=15.0, **_):
        assert (tool, arguments) == ("immich.asset_image", {"asset_id": "a-1"})
        return _preview()

    async def fake_put(member_sub, image, *, origin, thread_id=None, source_ref=None, now=None):
        stored.update(member_sub=member_sub, origin=origin, thread_id=thread_id,
                      source_ref=source_ref, size=(image.width, image.height))
        now = datetime.now(UTC)
        return ImageRow("3f2a9c01-0000-4000-8000-000000000001", member_sub, thread_id,
                        origin, source_ref, "image/jpeg", image.data, image.width,
                        image.height, None, now, now + timedelta(days=1))

    monkeypatch.setattr(immich, "invoke", fake_invoke)
    monkeypatch.setattr(immich.store, "put", fake_put)

    image_id = await immich.from_immich("sub-noah", "a-1", thread_id="t1")

    assert image_id == "3f2a9c01-0000-4000-8000-000000000001"
    assert stored == {"member_sub": "sub-noah", "origin": "immich", "thread_id": "t1",
                      "source_ref": "a-1", "size": (300, 400)}


async def test_immich_down_mints_nothing(monkeypatch):
    async def fake_invoke(*_a, **_k):
        return "error: eve-tools unavailable (ConnectError)"

    async def never_put(*_a, **_k):
        raise AssertionError("no id may be minted for an image we do not have")

    monkeypatch.setattr(immich, "invoke", fake_invoke)
    monkeypatch.setattr(immich.store, "put", never_put)
    assert await immich.from_immich("sub-noah", "a-1", thread_id="t1") is None


async def test_undecodable_bytes_mint_nothing(monkeypatch):
    async def fake_invoke(*_a, **_k):
        return json.dumps({"base64": base64.b64encode(b"nope").decode()})

    monkeypatch.setattr(immich, "invoke", fake_invoke)
    assert await immich.from_immich("sub-noah", "a-1", thread_id="t1") is None
