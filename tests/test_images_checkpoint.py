"""tests/test_images_checkpoint.py - the reason image references exist.

A turn with one photo must checkpoint no image bytes and grow the checkpoint
by less than 1 KB over the same turn without the photo (spec 6)."""
from __future__ import annotations

import base64
import io
from datetime import UTC, datetime, timedelta

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from PIL import Image

from eve.family import Family
from eve.graph import build_graph
from eve.images.process import normalise
from eve.images.store import ImageRow
from tests.conftest import FakeToolCallingModel
from tests.test_graph import CONFIG, NOAH, _no_extract, _no_recall, _no_suggest

IMAGE_ID = "aaaaaaaa-0000-4000-8000-000000000001"


def _row(image_id, caption=None):
    # A real 400x300 photo, not `b"JPEG-a"": the checkpoint-growth assertion
    # below only means something if the byte search has real pixel data to
    # search for. Built the same way as Task 1's `_image` helper.
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (10, 120, 200)).save(buf, "JPEG")
    normalised = normalise(buf.getvalue())
    now = datetime.now(UTC)
    return ImageRow(image_id, "sub-noah", "t1", "upload", None,
                    normalised.content_type, normalised.data,
                    normalised.width, normalised.height, caption, now,
                    now + timedelta(days=30))


async def _checkpoint_bytes(monkeypatch, human, thread) -> tuple[int, bytes]:
    saver = MemorySaver()
    app = build_graph(
        model_factory=lambda _t: FakeToolCallingModel(messages=iter([AIMessage("Yes.")])),
        recall_fn=_no_recall, extract_fn=_no_extract, suggest_fn=_no_suggest,
    ).compile(checkpointer=saver)
    config = {"configurable": {**CONFIG["configurable"], "thread_id": thread}}
    await app.ainvoke({"messages": [human]}, config)
    checkpoint = await saver.aget_tuple(config)
    blob = saver.serde.dumps_typed(checkpoint.checkpoint)[1]
    return len(blob), blob


async def test_a_photo_turn_checkpoints_a_reference_not_pixels(monkeypatch):
    from eve.images import hydrate as hydrate_module
    from eve.models import TIER_VISION, Tier

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")
    monkeypatch.setattr("eve.graph.TIER_VISION", {**TIER_VISION, Tier.VOICE: True})
    row = _row(IMAGE_ID)

    async def fake_get(image_id, member_sub, *, include_expired=False, now=None):
        return row

    monkeypatch.setattr(hydrate_module.store, "get", fake_get)

    plain, _ = await _checkpoint_bytes(monkeypatch, HumanMessage("does this go?"), "t-plain")
    photo, blob = await _checkpoint_bytes(monkeypatch, HumanMessage(content=[
        {"type": "text", "text": "does this go?"},
        {"type": "eve_image", "image_id": IMAGE_ID, "alt": "photo sent by Noah"},
    ]), "t-photo")

    assert base64.b64encode(row.bytes) not in blob
    assert row.bytes not in blob
    assert photo - plain < 1024
