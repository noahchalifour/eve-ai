"""tests/test_images_store.py - ownership is enforced here or not at all:
every statement carries member_sub, and a foreign row is a missing row."""
from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from eve.images.process import normalise

pytestmark = pytest.mark.integration


def _image(colour=(10, 120, 200), size=(40, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "JPEG")
    return normalise(buf.getvalue())


@pytest.fixture(autouse=True)
async def pool(monkeypatch):
    monkeypatch.setenv("EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve")
    from eve.memory import db
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    p = await db.get_pool()
    async with p.connection() as conn:
        await conn.execute("TRUNCATE eve_image")
    yield p
    await db.close_pool()


async def test_an_upload_round_trips_for_its_owner():
    from eve.images import store

    row = await store.put("sub-noah", _image(), origin="upload", thread_id="t1")
    got = await store.get(row.id, "sub-noah")

    assert got is not None
    assert got.bytes == row.bytes
    assert (got.width, got.height) == (40, 30)
    assert got.expires_at - got.created_at == timedelta(days=30)


async def test_another_member_gets_nothing():
    from eve.images import store

    row = await store.put("sub-noah", _image(), origin="upload", thread_id="t1")
    assert await store.get(row.id, "sub-kid") is None


async def test_an_expired_row_is_hidden_unless_asked_for():
    from eve.images import store

    row = await store.put("sub-noah", _image(), origin="upload", thread_id="t1")
    later = datetime.now(UTC) + timedelta(days=31)
    assert await store.get(row.id, "sub-noah", now=later) is None
    assert await store.get(row.id, "sub-noah", include_expired=True, now=later) is not None


async def test_immich_copies_dedupe_on_source_ref_and_refresh():
    from eve.images import store

    first = await store.put("sub-noah", _image(), origin="immich", source_ref="a-1", thread_id="t1")
    second = await store.put(
        "sub-noah", _image(colour=(1, 2, 3)), origin="immich", source_ref="a-1", thread_id="t2"
    )

    assert second.id == first.id
    assert second.bytes != first.bytes
    assert second.thread_id == "t2"
    assert second.expires_at - second.created_at <= timedelta(days=1, seconds=5)


async def test_the_same_asset_for_two_members_is_two_rows():
    from eve.images import store

    a = await store.put("sub-noah", _image(), origin="immich", source_ref="a-1")
    b = await store.put("sub-kid", _image(), origin="immich", source_ref="a-1")
    assert a.id != b.id


async def test_resolve_accepts_full_and_unique_short_ids_in_the_thread():
    from eve.images import store

    row = await store.put("sub-noah", _image(), origin="upload", thread_id="t1")

    assert (await store.resolve(row.id, "sub-noah", "t1")).id == row.id
    assert (await store.resolve(store.short_id(row.id), "sub-noah", "t1")).id == row.id


async def test_resolve_refuses_other_members_other_threads_and_junk():
    from eve.images import store

    row = await store.put("sub-noah", _image(), origin="upload", thread_id="t1")

    assert await store.resolve(row.id, "sub-kid", "t1") is None
    assert await store.resolve(row.id, "sub-noah", "t2") is None
    assert await store.resolve("not-an-id", "sub-noah", "t1") is None
    assert await store.resolve("", "sub-noah", "t1") is None


async def test_resolve_refuses_an_ambiguous_short_id(pool):
    from eve.images import store

    # Force two ids sharing a prefix; random UUIDs almost never collide on
    # 8 hex characters, which is exactly why a collision must be refused
    # rather than guessed.
    a = "abcdef01-0000-4000-8000-000000000001"
    b = "abcdef01-0000-4000-8000-000000000002"
    async with pool.connection() as conn:
        for image_id in (a, b):
            await conn.execute(
                "INSERT INTO eve_image (id, member_sub, thread_id, origin,"
                " content_type, bytes, width, height, expires_at)"
                " VALUES (%s, 'sub-noah', 't1', 'upload', 'image/jpeg', '\\x00',"
                " 1, 1, now() + interval '1 day')",
                (image_id,),
            )
    assert await store.resolve("abcdef01", "sub-noah", "t1") is None


async def test_set_caption_is_owner_scoped():
    from eve.images import store

    row = await store.put("sub-noah", _image(), origin="upload", thread_id="t1")
    await store.set_caption(row.id, "sub-kid", "stolen")
    assert (await store.get(row.id, "sub-noah")).caption is None
    await store.set_caption(row.id, "sub-noah", "a blue rectangle")
    assert (await store.get(row.id, "sub-noah")).caption == "a blue rectangle"


async def test_sweep_deletes_only_expired_rows():
    from eve.images import store

    keep = await store.put("sub-noah", _image(), origin="upload", thread_id="t1")
    gone = await store.put("sub-noah", _image(), origin="immich", source_ref="a-9")

    deleted = await store.sweep(now=datetime.now(UTC) + timedelta(days=2))

    assert deleted == 1
    assert await store.get(keep.id, "sub-noah") is not None
    assert await store.get(gone.id, "sub-noah", include_expired=True) is None
