"""tests/test_images_hydrate.py - the one function that turns references into
pixels, right before a model call and never into state."""
from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from eve.images import hydrate as hydrate_module
from eve.images.hydrate import UNAVAILABLE, hydrate, reference
from eve.images.store import ImageRow

A = "aaaaaaaa-0000-4000-8000-000000000001"
B = "bbbbbbbb-0000-4000-8000-000000000002"


def _row(image_id, caption=None):
    now = datetime.now(UTC)
    return ImageRow(image_id, "sub-noah", "t1", "upload", None, "image/jpeg",
                    b"JPEG-" + image_id[:1].encode(), 10, 10, caption, now,
                    now + timedelta(days=30))


@pytest.fixture
def rows(monkeypatch):
    table = {A: _row(A), B: _row(B, caption="a red jumper")}
    seen = []

    async def fake_get(image_id, member_sub, *, include_expired=False, now=None):
        seen.append((image_id, member_sub))
        return table.get(image_id) if member_sub == "sub-noah" else None

    monkeypatch.setattr(hydrate_module.store, "get", fake_get)
    return seen


def _human(text, *ids):
    return HumanMessage(content=[{"type": "text", "text": text},
                                 *(reference(i, "photo") for i in ids)])


async def test_native_hydration_labels_then_inlines_pixels(rows):
    out = await hydrate([_human("look", A)], "sub-noah", native=True, window=2)
    assert out[0].content == [
        {"type": "text", "text": "look"},
        {"type": "text", "text": "[image aaaaaaaa]"},
        {"type": "image", "base64": base64.b64encode(b"JPEG-a").decode(),
         "mime_type": "image/jpeg"},
    ]


async def test_the_input_is_never_mutated(rows):
    messages = [_human("look", A)]
    before = [dict(block) for block in messages[0].content]
    await hydrate(messages, "sub-noah", native=True, window=2)
    assert messages[0].content == before


async def test_only_the_last_window_turns_get_pixels(rows):
    messages = [_human("old", A), AIMessage("ok"), _human("new", B)]
    out = await hydrate(messages, "sub-noah", native=True, window=1)
    assert out[0].content[1] == {"type": "text", "text": "[image aaaaaaaa: photo]"}
    assert out[2].content[2]["type"] == "image"
    assert [image_id for image_id, _ in rows] == [B]  # the old one is never fetched
    assert out[1] is messages[1]


async def test_missing_foreign_or_expired_becomes_the_placeholder(rows):
    missing = "cccccccc-0000-4000-8000-000000000003"
    out = await hydrate([_human("look", missing)], "sub-noah", native=True, window=2)
    assert out[0].content[1] == {"type": "text", "text": UNAVAILABLE}
    out = await hydrate([_human("look", A)], "sub-kid", native=True, window=2)
    assert out[0].content[1] == {"type": "text", "text": UNAVAILABLE}


async def test_no_principal_touches_nothing(rows):
    out = await hydrate([_human("look", A)], None, native=True, window=2)
    assert out[0].content[1] == {"type": "text", "text": UNAVAILABLE}
    assert rows == []


async def test_caption_path_uses_the_cached_caption(rows):
    out = await hydrate([_human("look", B)], "sub-noah", native=False, window=2)
    assert out[0].content[1:] == [
        {"type": "text", "text": "[image bbbbbbbb: a red jumper]"}]


async def test_caption_path_generates_and_caches_a_missing_caption(rows, monkeypatch):
    stored = {}

    async def fake_describe(row):
        return "a white shirt"

    async def fake_set_caption(image_id, member_sub, caption):
        stored[image_id] = caption

    monkeypatch.setattr(hydrate_module, "_describe", fake_describe)
    monkeypatch.setattr(hydrate_module.store, "set_caption", fake_set_caption)
    out = await hydrate([_human("look", A)], "sub-noah", native=False, window=2)
    assert out[0].content[1] == {"type": "text", "text": "[image aaaaaaaa: a white shirt]"}
    assert stored == {A: "a white shirt"}


async def test_plain_string_humans_and_ai_messages_pass_through_by_identity(rows):
    messages = [HumanMessage("hi"), AIMessage("hello")]
    out = await hydrate(messages, "sub-noah", native=True, window=2)
    assert out[0] is messages[0] and out[1] is messages[1]
    assert rows == []
