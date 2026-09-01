# Personal Stylist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eve gains a stylist specialist that reads a catalogue of the clothes you own, checks the weather and your calendar, and tells you what to wear.

**Architecture:** An Immich album is catalogued once per garment by a vision pass into a Postgres table; the stylist reads that catalogue as text, so no image ever enters Eve's graph or a specialist loop. Along the way, skills gain a `specialist:` scope so each specialist can search its own procedures and Eve cannot see them.

**Tech Stack:** Python 3.13, FastAPI (`eve-tools`), LangChain/LangGraph, psycopg 3 + Alembic, pydantic-settings, httpx, pytest (asyncio auto mode).

**Spec:** `docs/superpowers/specs/2026-09-01-eve-stylist-design.md`

## Global Constraints

- **Pixels never leave the sync path.** No image bytes may enter `EveState`, a specialist's loop, or an Aegra checkpoint. Base64 images exist only between `eve-tools` and `eve.wardrobe.catalog`/`eve.wardrobe.vision`.
- **Third-party credentials live only in `eve-tools`** (ADR 0006). The Immich URL and API key go in `ToolsSettings`, never in `eve.settings.Settings`.
- **Model tiers are named only in `src/eve/models.py`.** Use `Tier.REFLEX` for the vision pass and `Tier.MECHANICAL` (via `build_specialist`'s default) for the stylist loop. Never write a model id anywhere else.
- **Every tool returns a string and never raises.** `eve.tools_client.invoke` already degrades failures to a string starting with `error:`; new tools must preserve that posture, because a raised exception fails the whole turn instead of letting Eve explain.
- **Permission checks happen in Eve's main container, before the HTTP call** (ADR 0006), using `eve.specialists.permissions.permission_denial`.
- **Tests are async by default.** `pytest.ini` options set asyncio auto mode; test functions are `async def` with no decorator. Database tests carry `pytestmark = pytest.mark.integration` and use `postgresql://eve:eve@127.0.0.1:15432/eve`.
- **`ponytail:` comments mark deliberate ceilings**, naming the ceiling and the upgrade path.

## Deviation from the spec, decided here

The spec lists `src/eve/wardrobe/` as three files (`store.py`, `catalog.py`, `cli.py`). This plan makes it four: the vision pass moves into its own `vision.py`. It is the one part with a model call, a pydantic schema, and a prompt file, and it is the part most likely to be retuned — keeping it out of the sync orchestration keeps both files small enough to hold in context. Nothing else about the spec changes.

## Task Order and Dependencies

```
Task 1 (Immich in eve-tools) ─┐
Task 2 (table + store) ───────┼─→ Task 5 (sync) ─→ Task 6 (CLI) ─┐
Task 3 (wardrobe_album) ──────┤                                   ├─→ Task 9 (stylist) ─→ Task 10 (wire up)
Task 4 (vision pass) ─────────┘                                   │
Task 7 (scoped skills registry) ─→ Task 8 (specialist search) ────┘
```

Tasks 7 and 8 are independent of 1–6 and may be done first if preferred.

---

### Task 1: Immich client in `eve-tools`

`eve-tools` is the only process holding third-party credentials. This task adds the two Immich calls the catalogue needs and nothing else.

**Files:**
- Create: `src/eve_tools/immich.py`
- Modify: `src/eve_tools/settings.py` (add two fields to `ToolsSettings`)
- Modify: `src/eve_tools/app.py` (add two entries to `_HANDLERS`, add the import)
- Modify: `.env.example`
- Test: `tests/test_eve_tools_immich.py`
- Test: `tests/test_eve_tools_app.py` (add one dispatch test)

**Interfaces:**
- Consumes: `eve_tools.settings.get_tools_settings()`.
- Produces:
  - `async def album_assets(album_id: str) -> dict` returning `{"assets": [{"id": str, "filename": str}, ...]}`
  - `async def asset_image(asset_id: str) -> dict` returning `{"asset_id": str, "content_type": str, "base64": str}`
  - Handler names `immich.album_assets` (argument `album_id`) and `immich.asset_image` (argument `asset_id`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_eve_tools_immich.py`:

```python
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
```

Add to `tests/test_eve_tools_app.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_eve_tools_immich.py tests/test_eve_tools_app.py::test_invoke_dispatches_immich_album_assets -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve_tools.immich'`

- [ ] **Step 3: Add the settings fields**

In `src/eve_tools/settings.py`, inside `ToolsSettings`, after `monarch_mfa_secret`:

```python
    # The wardrobe's photo library. Read-only use: the catalogue never writes
    # to Immich, so an API key scoped to reading is sufficient.
    immich_url: str = ""
    immich_api_key: str = ""
```

- [ ] **Step 4: Write `src/eve_tools/immich.py`**

```python
"""Immich REST client. No SDK: two GETs behind a long-lived API key, and a
dependency buys nothing over httpx for that - the same reasoning
`home_assistant.py` gives for Home Assistant.

Read-only by construction. The wardrobe catalogue is downstream of the album;
nothing here writes to Immich, so a key scoped to reading is enough.
"""

from __future__ import annotations

import base64

import httpx

from eve_tools.settings import get_tools_settings

# ponytail: a flat cap, not pagination. A wardrobe album past this is not a
# wardrobe. Raise it, or teach the catalogue to page, if one ever overflows.
_MAX_ASSETS = 500

# Tests substitute an httpx.MockTransport here. Production leaves it None and
# every client is built with real networking - one seam rather than threading
# a client object through both public functions.
_transport_for_test: httpx.MockTransport | None = None


def _client(timeout: float) -> httpx.AsyncClient:
    settings = get_tools_settings()
    return httpx.AsyncClient(
        timeout=timeout,
        base_url=settings.immich_url,
        headers={"x-api-key": settings.immich_api_key},
        transport=_transport_for_test,
    )


async def album_assets(album_id: str) -> dict:
    """Every asset in one album: id and original filename, nothing else.

    Immich's album payload carries the full EXIF and people blob per asset,
    which is noise to a catalogue that only needs to know which assets exist
    and which of them it has already seen.
    """
    async with _client(15.0) as client:
        response = await client.get(f"/api/albums/{album_id}")
        response.raise_for_status()
        album = response.json()
    assets = album.get("assets") or []
    return {
        "assets": [
            {"id": asset["id"], "filename": asset.get("originalFileName", "")}
            for asset in assets[:_MAX_ASSETS]
        ]
    }


async def asset_image(asset_id: str) -> dict:
    """One asset's preview, base64-encoded.

    `size=preview` is the large JPEG rather than the grid thumbnail: a
    thumbnail is too small to read a fabric or a weave off, which is most of
    what the vision pass is for.
    """
    async with _client(30.0) as client:
        response = await client.get(
            f"/api/assets/{asset_id}/thumbnail", params={"size": "preview"}
        )
        response.raise_for_status()
        content = response.content
        content_type = response.headers.get("content-type", "image/jpeg")
    return {
        "asset_id": asset_id,
        "content_type": content_type,
        "base64": base64.b64encode(content).decode(),
    }
```

- [ ] **Step 5: Register the handlers**

In `src/eve_tools/app.py`, change the import line:

```python
from eve_tools import (
    caldav_client,
    gmail,
    home_assistant,
    immich,
    mcp_dispatch,
    monarch,
)
```

and add to `_HANDLERS`, after the `finances.*` entries:

```python
    "immich.album_assets": lambda a: immich.album_assets(a["album_id"]),
    "immich.asset_image": lambda a: immich.asset_image(a["asset_id"]),
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_eve_tools_immich.py tests/test_eve_tools_app.py -v`
Expected: PASS

- [ ] **Step 7: Document the two new environment variables**

In `.env.example`, in the `eve-tools` section alongside the Home Assistant and Monarch entries:

```bash
# Immich — the photo library holding the wardrobe albums. Read-only use.
# Create the key in Immich under Account Settings -> API Keys.
EVE_TOOLS_IMMICH_URL=http://immich.immich.svc.cluster.local:2283
EVE_TOOLS_IMMICH_API_KEY=
```

- [ ] **Step 8: Commit**

```bash
git add src/eve_tools/immich.py src/eve_tools/settings.py src/eve_tools/app.py \
        .env.example tests/test_eve_tools_immich.py tests/test_eve_tools_app.py
git commit -m "feat(eve-tools): add the immich.* namespace for wardrobe photos"
```

---

### Task 2: The `eve_wardrobe_item` table and its store

**Files:**
- Create: `alembic/versions/0005_eve_wardrobe_item.py`
- Create: `src/eve/wardrobe/__init__.py` (empty)
- Create: `src/eve/wardrobe/store.py`
- Test: `tests/test_wardrobe_store.py`

**Interfaces:**
- Consumes: `eve.memory.db.get_pool`.
- Produces:
  - `async def catalogued_asset_ids(member_sub: str) -> set[str]`
  - `async def insert_items(member_sub: str, asset_id: str, items: list[dict]) -> None` — each item is `{"name": str, "category": str, "attrs": dict}`; `item_index` is assigned by position.
  - `async def delete_assets(member_sub: str, asset_ids: list[str]) -> None`
  - `async def list_items(member_sub: str) -> list[dict]` — ordered by `category`, then `name`.
  - `async def count_items(member_sub: str) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_wardrobe_store.py`:

```python
"""tests/test_wardrobe_store.py"""
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
async def pool(monkeypatch):
    monkeypatch.setenv("EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve")
    from eve.memory import db
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    p = await db.get_pool()
    async with p.connection() as conn:
        await conn.execute("TRUNCATE eve_wardrobe_item")
    yield p
    await db.close_pool()


async def test_inserting_two_garments_from_one_asset(pool):
    from eve.wardrobe import store

    await store.insert_items(
        "sub-noah",
        "asset-1",
        [
            {"name": "white oxford shirt", "category": "top", "attrs": {"warmth": 2}},
            {"name": "blue oxford shirt", "category": "top", "attrs": {"warmth": 2}},
        ],
    )

    items = await store.list_items("sub-noah")
    assert [i["name"] for i in items] == ["blue oxford shirt", "white oxford shirt"]
    assert {i["item_index"] for i in items} == {0, 1}
    assert items[0]["attrs"] == {"warmth": 2}
    assert await store.count_items("sub-noah") == 2


async def test_catalogued_asset_ids_are_scoped_to_the_member(pool):
    from eve.wardrobe import store

    await store.insert_items(
        "sub-noah", "asset-1", [{"name": "blazer", "category": "outerwear", "attrs": {}}]
    )
    await store.insert_items(
        "sub-kendra", "asset-2", [{"name": "coat", "category": "outerwear", "attrs": {}}]
    )

    assert await store.catalogued_asset_ids("sub-noah") == {"asset-1"}
    assert await store.catalogued_asset_ids("sub-kendra") == {"asset-2"}


async def test_deleting_assets_removes_every_garment_from_them(pool):
    from eve.wardrobe import store

    await store.insert_items(
        "sub-noah",
        "asset-1",
        [
            {"name": "shirt a", "category": "top", "attrs": {}},
            {"name": "shirt b", "category": "top", "attrs": {}},
        ],
    )
    await store.insert_items(
        "sub-noah", "asset-2", [{"name": "boots", "category": "footwear", "attrs": {}}]
    )

    await store.delete_assets("sub-noah", ["asset-1"])

    assert [i["name"] for i in await store.list_items("sub-noah")] == ["boots"]


async def test_deleting_nothing_is_not_an_error(pool):
    from eve.wardrobe import store

    await store.delete_assets("sub-noah", [])
    assert await store.count_items("sub-noah") == 0


async def test_reinserting_the_same_asset_replaces_its_rows(pool):
    from eve.wardrobe import store

    await store.insert_items(
        "sub-noah", "asset-1", [{"name": "old name", "category": "top", "attrs": {}}]
    )
    await store.insert_items(
        "sub-noah", "asset-1", [{"name": "new name", "category": "top", "attrs": {}}]
    )

    assert [i["name"] for i in await store.list_items("sub-noah")] == ["new name"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose -f docker-compose.test.yml up -d && uv run pytest tests/test_wardrobe_store.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve.wardrobe'`

- [ ] **Step 3: Write the migration**

Create `alembic/versions/0005_eve_wardrobe_item.py`:

```python
"""One row per garment, catalogued from an Immich asset by a vision pass.

Revision ID: 0005_eve_wardrobe_item
Revises: 0004_eve_computer_task

`item_index` exists because one photograph is not always one garment: a rail
of shirts or a folded stack is a natural thing to shoot, and a schema keyed
on the asset alone silently discards everything but the first item.

`attrs` is jsonb rather than columns because nothing queries on fabric. The
whole table is read at once and rendered as text for a prompt, and the vision
prompt's vocabulary will drift as it is tuned; a migration per drift buys
nothing. `name` and `category` stay real columns - they are the two stable
axes, used for grouping the rendered catalogue and for `eve-wardrobe list`.
"""
from alembic import op

revision = "0005_eve_wardrobe_item"
down_revision = "0004_eve_computer_task"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_wardrobe_item (
          id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
          member_sub     text        NOT NULL,
          asset_id       text        NOT NULL,
          item_index     int         NOT NULL DEFAULT 0,
          name           text        NOT NULL,
          category       text        NOT NULL,
          attrs          jsonb       NOT NULL DEFAULT '{}'::jsonb,
          catalogued_at  timestamptz NOT NULL DEFAULT now(),
          UNIQUE (member_sub, asset_id, item_index)
        )
        """
    )
    # Every read is "this member's whole wardrobe" (eve.wardrobe.store).
    op.execute(
        "CREATE INDEX eve_wardrobe_item_member"
        " ON eve_wardrobe_item (member_sub, category, name)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE eve_wardrobe_item")
```

- [ ] **Step 4: Write the store**

Create `src/eve/wardrobe/__init__.py` as an empty file, then `src/eve/wardrobe/store.py`:

```python
"""Every eve_wardrobe_item SQL statement. Same discipline as
`eve/computer/store.py`: one module owns the table, and nothing else in the
codebase writes SQL against it.
"""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.memory.db import get_pool


async def catalogued_asset_ids(member_sub: str) -> set[str]:
    """Which assets this member's catalogue has already seen. The sync diffs
    the album against this rather than re-describing every photograph."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DISTINCT asset_id FROM eve_wardrobe_item WHERE member_sub = %s",
                (member_sub,),
            )
            return {row[0] for row in await cur.fetchall()}


async def insert_items(member_sub: str, asset_id: str, items: list[dict]) -> None:
    """Replace everything catalogued from one asset.

    Delete-then-insert rather than upsert: a re-describe can return a
    different NUMBER of garments than last time (the prompt got better at
    seeing the second shirt on the rail), and an upsert keyed on item_index
    would leave the surplus rows from the longer previous run behind.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM eve_wardrobe_item WHERE member_sub = %s AND asset_id = %s",
            (member_sub, asset_id),
        )
        for index, item in enumerate(items):
            await conn.execute(
                "INSERT INTO eve_wardrobe_item"
                " (member_sub, asset_id, item_index, name, category, attrs)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    member_sub,
                    asset_id,
                    index,
                    item["name"],
                    item["category"],
                    Jsonb(item.get("attrs") or {}),
                ),
            )


async def delete_assets(member_sub: str, asset_ids: list[str]) -> None:
    """Assets that have left the album. A no-op on an empty list rather than
    an `IN ()` syntax error."""
    if not asset_ids:
        return
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM eve_wardrobe_item"
            " WHERE member_sub = %s AND asset_id = ANY(%s)",
            (member_sub, list(asset_ids)),
        )


async def list_items(member_sub: str) -> list[dict]:
    """The whole wardrobe, grouped-ready. Ordering is by category then name so
    the rendered catalogue and `eve-wardrobe list` agree without either
    sorting again."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM eve_wardrobe_item WHERE member_sub = %s"
                " ORDER BY category, name",
                (member_sub,),
            )
            return list(await cur.fetchall())


async def count_items(member_sub: str) -> int:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) FROM eve_wardrobe_item WHERE member_sub = %s",
                (member_sub,),
            )
            row = await cur.fetchone()
            return int(row[0])
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_wardrobe_store.py -v -m integration`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0005_eve_wardrobe_item.py src/eve/wardrobe/ \
        tests/test_wardrobe_store.py
git commit -m "feat(wardrobe): add the eve_wardrobe_item table and its store"
```

---

### Task 3: `wardrobe_album` on the family roster

The album id is per-member, non-secret configuration whose change history should be a pull request — which is what `family.yaml` is for and says it is for.

**Files:**
- Modify: `src/eve/family.py` (add a field to `Member`, parse it in `from_yaml`)
- Modify: `family.yaml` (add the field to both adults, with a comment)
- Test: `tests/test_family.py`

**Interfaces:**
- Produces: `Member.wardrobe_album: str | None`, defaulting to `None` when the key is absent.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_family.py`:

```python
def test_a_member_without_a_wardrobe_album_gets_none(tmp_path):
    path = tmp_path / "family.yaml"
    path.write_text(
        "members:\n"
        "  - sub: 'sub-1'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
    )
    family = Family.from_yaml(path)
    assert family.get("sub-1").wardrobe_album is None


def test_a_wardrobe_album_is_read_from_the_roster(tmp_path):
    path = tmp_path / "family.yaml"
    path.write_text(
        "members:\n"
        "  - sub: 'sub-1'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    wardrobe_album: 'album-uuid-1'\n"
    )
    family = Family.from_yaml(path)
    assert family.get("sub-1").wardrobe_album == "album-uuid-1"
```

If `Family` is not already imported at the top of `tests/test_family.py`, add `from eve.family import Family`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_family.py -v -k wardrobe`
Expected: FAIL — `TypeError: Member.__init__() got an unexpected keyword argument` or `AttributeError: 'Member' object has no attribute 'wardrobe_album'`

- [ ] **Step 3: Add the field**

In `src/eve/family.py`, in the `Member` dataclass, after `permissions`:

```python
    # The Immich album holding this member's clothes. Optional: a member
    # without one has no wardrobe, which the stylist reports rather than
    # erroring on. Non-secret and per-member, so it belongs in the roster
    # rather than in settings.
    wardrobe_album: str | None = None
```

and in `Family.from_yaml`, inside the `Member(...)` construction, after `permissions=...`:

```python
                    wardrobe_album=entry.get("wardrobe_album"),
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_family.py -v`
Expected: PASS

- [ ] **Step 5: Add the field to the real roster**

In `family.yaml`, for Noah, after his `permissions` block:

```yaml
    # Phase 6: the Immich album holding his clothes, catalogued by
    # `eve-wardrobe sync`. Find the id in Immich's album URL.
    wardrobe_album: ""
```

and the same for Kendra. Leave both empty strings for now — an operator fills them in when the albums exist. Change the parse to treat empty as absent by editing the line added in Step 3 to:

```python
                    wardrobe_album=entry.get("wardrobe_album") or None,
```

- [ ] **Step 6: Run the full family suite**

Run: `uv run pytest tests/test_family.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/eve/family.py family.yaml tests/test_family.py
git commit -m "feat(family): add a per-member wardrobe_album to the roster"
```

---

### Task 4: The vision pass

One REFLEX-tier structured-output call per photograph, turning an image into a list of garment records.

**Files:**
- Create: `prompts/wardrobe.md`
- Create: `src/eve/wardrobe/vision.py`
- Test: `tests/test_wardrobe_vision.py`

**Interfaces:**
- Consumes: `eve.models.get_model`, `eve.models.Tier`.
- Produces:
  - `class WardrobeItem(BaseModel)` with fields `name, category, colour, pattern, fabric, warmth, formality, season, notes`
  - `class WardrobeItems(BaseModel)` with field `items: list[WardrobeItem]`
  - `async def describe(image_base64: str, content_type: str) -> list[WardrobeItem]`
  - `def to_row(item: WardrobeItem) -> dict` returning `{"name": str, "category": str, "attrs": dict}`
  - `CATEGORIES: tuple[str, ...]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_wardrobe_vision.py`:

```python
"""tests/test_wardrobe_vision.py"""
from unittest.mock import AsyncMock, MagicMock

from eve.wardrobe import vision


def _model_returning(items):
    """A stand-in for `get_model(...).with_structured_output(WardrobeItems)`."""
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=vision.WardrobeItems(items=items))
    model = MagicMock()
    model.with_structured_output = MagicMock(return_value=structured)
    return model, structured


async def test_describe_returns_the_models_items(monkeypatch):
    item = vision.WardrobeItem(
        name="navy wool blazer",
        category="outerwear",
        colour="navy",
        pattern="plain",
        fabric="wool",
        warmth=4,
        formality=4,
        season="autumn/winter",
        notes="single-breasted, notch lapel",
    )
    model, _ = _model_returning([item])
    monkeypatch.setattr(vision, "get_model", lambda _tier: model)

    result = await vision.describe("aGVsbG8=", "image/jpeg")

    assert [i.name for i in result] == ["navy wool blazer"]


async def test_describe_sends_the_image_as_a_data_url(monkeypatch):
    model, structured = _model_returning([])
    monkeypatch.setattr(vision, "get_model", lambda _tier: model)

    await vision.describe("aGVsbG8=", "image/png")

    messages = structured.ainvoke.await_args.args[0]
    blocks = messages[-1].content
    image_block = next(b for b in blocks if b["type"] == "image_url")
    assert image_block["image_url"]["url"] == "data:image/png;base64,aGVsbG8="
    text_block = next(b for b in blocks if b["type"] == "text")
    assert "garment" in text_block["text"].lower()


async def test_describe_runs_on_the_reflex_tier(monkeypatch):
    from eve.models import Tier

    seen = []
    model, _ = _model_returning([])

    def _get_model(tier):
        seen.append(tier)
        return model

    monkeypatch.setattr(vision, "get_model", _get_model)

    await vision.describe("aGVsbG8=", "image/jpeg")

    assert seen == [Tier.REFLEX]


async def test_an_unknown_category_is_coerced_to_accessory(monkeypatch):
    item = vision.WardrobeItem(
        name="thing",
        category="spacesuit",
        colour="silver",
        pattern="plain",
        fabric="nylon",
        warmth=3,
        formality=1,
        season="all",
        notes="",
    )
    model, _ = _model_returning([item])
    monkeypatch.setattr(vision, "get_model", lambda _tier: model)

    result = await vision.describe("aGVsbG8=", "image/jpeg")

    assert result[0].category == "accessory"


def test_to_row_splits_the_two_stable_columns_from_attrs():
    item = vision.WardrobeItem(
        name="brown chelsea boots",
        category="footwear",
        colour="brown",
        pattern="plain",
        fabric="leather",
        warmth=3,
        formality=3,
        season="all",
        notes="elastic gusset",
    )

    row = vision.to_row(item)

    assert row["name"] == "brown chelsea boots"
    assert row["category"] == "footwear"
    assert row["attrs"] == {
        "colour": "brown",
        "pattern": "plain",
        "fabric": "leather",
        "warmth": 3,
        "formality": 3,
        "season": "all",
        "notes": "elastic gusset",
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_wardrobe_vision.py -v`
Expected: FAIL — `ImportError: cannot import name 'vision' from 'eve.wardrobe'`

- [ ] **Step 3: Write the prompt**

Create `prompts/wardrobe.md`:

```markdown
You are cataloguing one photograph from a family member's wardrobe.

Describe every distinct garment or accessory visible in the photograph. Most
photographs show exactly one item; some show a rail, a shelf, or a folded
stack, and every item in those is a separate entry. Ignore anything that is
not clothing — hangers, furniture, the floor, the person wearing it.

For each item:

- **name** — a short, specific, spoken-aloud label the owner would recognise:
  "navy wool blazer", "white oxford shirt", "brown chelsea boots". Colour plus
  material plus garment type is usually right. Never a brand you are guessing
  at, never a serial number, never "item 1".
- **category** — exactly one of: top, bottom, outerwear, footwear, accessory,
  full. Use `full` for a one-piece garment such as a dress, a jumpsuit, or a
  suit photographed as a unit.
- **colour** — the dominant colour in plain words. Add a secondary colour only
  if the item genuinely reads as two.
- **pattern** — plain, striped, checked, floral, printed, or a short phrase.
- **fabric** — your best read of the material: cotton, wool, linen, denim,
  leather, synthetic, knit. Say "uncertain" rather than inventing one.
- **warmth** — 1 to 5. 1 is a summer tee, 3 is a mid-weight jumper, 5 is a
  winter parka. Judge the garment, not the weather in the photo.
- **formality** — 1 to 5. 1 is loungewear, 3 is smart casual, 5 is black tie.
- **season** — when it is wearable: "summer", "autumn/winter", "all", or
  similar.
- **notes** — one short clause about fit or detail that would help choose
  between two similar items: "cropped", "notch lapel", "elastic gusset".
  Leave empty if there is nothing distinguishing.

Describe only what you can see. If the photograph is too dark, too blurred, or
too far away to identify a garment, return no items for it rather than
guessing — an empty result is recoverable, an invented coat is not.
```

- [ ] **Step 4: Write `src/eve/wardrobe/vision.py`**

```python
"""One REFLEX-tier structured-output call per photograph.

REFLEX rather than MECHANICAL because `models.py` says so in as many words:
that tier rides the metered Google key specifically so high-volume grinding
does not consume the ChatGPT subscription limits Noah uses for his own work.
Cataloguing a hundred-garment wardrobe is exactly that shape of work.

The third use of a pattern `memory/extract.py` and `suggest.py` already
establish: `get_model(tier).with_structured_output(Model)`.

This module is where pixels stop. `describe` takes base64 in and returns
garment records out; nothing downstream of it ever sees an image, which is
what keeps `build_specialist` unmodified and Aegra's checkpoints small.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from eve.models import Tier, get_model

logger = logging.getLogger(__name__)

CATEGORIES = ("top", "bottom", "outerwear", "footwear", "accessory", "full")

_PROMPT_FILE = Path("prompts/wardrobe.md")


class WardrobeItem(BaseModel):
    """One garment. Field descriptions are the model's instructions, so they
    are written for it rather than for a reader of this file - the prompt
    carries the longer version."""

    name: str = Field(description="Short spoken-aloud label, e.g. 'navy wool blazer'.")
    category: str = Field(description="One of: top, bottom, outerwear, footwear, accessory, full.")
    colour: str = Field(default="", description="Dominant colour in plain words.")
    pattern: str = Field(default="", description="plain, striped, checked, floral, printed.")
    fabric: str = Field(default="", description="cotton, wool, linen, denim, leather, knit, synthetic, uncertain.")
    warmth: int = Field(default=3, ge=1, le=5, description="1 summer tee, 5 winter parka.")
    formality: int = Field(default=3, ge=1, le=5, description="1 loungewear, 5 black tie.")
    season: str = Field(default="all", description="summer, autumn/winter, all.")
    notes: str = Field(default="", description="One short clause about fit or detail.")


class WardrobeItems(BaseModel):
    """Takes a wrapping object, not a bare list, for the reason
    `suggest.Suggestions` documents: `with_structured_output` wants a schema
    with named fields, and a top-level array is not reliably one."""

    items: list[WardrobeItem] = Field(default_factory=list)


@lru_cache(maxsize=1)
def _prompt() -> str:
    return _PROMPT_FILE.read_text()


def _coerce_category(category: str) -> str:
    """The model is told the six categories and mostly obeys. `accessory` is
    the safe landing place for the rest: a miscatalogued scarf is a nuisance,
    a row with a category nothing groups by is invisible in the rendered
    wardrobe."""
    lowered = (category or "").strip().lower()
    if lowered in CATEGORIES:
        return lowered
    logger.info("coercing unknown wardrobe category %r to 'accessory'", category)
    return "accessory"


async def describe(image_base64: str, content_type: str) -> list[WardrobeItem]:
    """Every garment visible in one photograph. An empty list is a valid and
    expected answer - the prompt tells the model to prefer it over guessing."""
    model = get_model(Tier.REFLEX).with_structured_output(WardrobeItems)
    message = HumanMessage(
        content=[
            {"type": "text", "text": _prompt()},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{content_type};base64,{image_base64}"},
            },
        ]
    )
    result = await model.ainvoke([message])
    for item in result.items:
        item.category = _coerce_category(item.category)
    return list(result.items)


def to_row(item: WardrobeItem) -> dict:
    """Split the two stable columns from the jsonb blob. The split lives here,
    beside the schema it splits, so a new field added above lands in `attrs`
    without anyone touching `store.py`."""
    attrs = item.model_dump(exclude={"name", "category"})
    return {"name": item.name, "category": item.category, "attrs": attrs}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_wardrobe_vision.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add prompts/wardrobe.md src/eve/wardrobe/vision.py tests/test_wardrobe_vision.py
git commit -m "feat(wardrobe): describe a garment photo on the REFLEX tier"
```

---

### Task 5: The sync, and rendering the wardrobe as text

**Files:**
- Create: `src/eve/wardrobe/catalog.py`
- Test: `tests/test_wardrobe_catalog.py`

**Interfaces:**
- Consumes: `eve.wardrobe.store` (Task 2), `eve.wardrobe.vision` (Task 4), `eve.family.get_family` with `Member.wardrobe_album` (Task 3), `eve.tools_client.invoke`, handler names from Task 1.
- Produces:
  - `def album_for(member_sub: str) -> str | None`
  - `async def sync(member_sub: str, *, force: bool = False, limit: int | None = None) -> dict` returning `{"catalogued": int, "removed": int, "failed": int, "remaining": int, "error": str | None}`
  - `async def render_wardrobe(member_sub: str) -> str`
  - `NO_ALBUM: str`, `EMPTY: str` — the two sentinel strings the stylist's tools surface verbatim.

- [ ] **Step 1: Write the failing test**

Create `tests/test_wardrobe_catalog.py`:

```python
"""tests/test_wardrobe_catalog.py"""
import json
from unittest.mock import AsyncMock

from eve.wardrobe import catalog, vision


def _item(name, category="top", **attrs):
    return vision.WardrobeItem(name=name, category=category, **attrs)


def _fake_invoke(album_assets, images=None):
    """Stands in for eve.tools_client.invoke, which returns a JSON STRING."""
    images = images or {}

    async def _invoke(tool, arguments, **kwargs):
        if tool == "immich.album_assets":
            return json.dumps({"assets": album_assets})
        if tool == "immich.asset_image":
            asset_id = arguments["asset_id"]
            return json.dumps(
                images.get(
                    asset_id,
                    {"asset_id": asset_id, "content_type": "image/jpeg", "base64": "aGk="},
                )
            )
        raise AssertionError(f"unexpected tool {tool}")

    return AsyncMock(side_effect=_invoke)


def _patch_common(monkeypatch, *, album="album-1", catalogued=frozenset()):
    monkeypatch.setattr(catalog, "album_for", lambda _sub: album)
    monkeypatch.setattr(
        catalog.store, "catalogued_asset_ids", AsyncMock(return_value=set(catalogued))
    )
    inserted = []

    async def _insert(member_sub, asset_id, items):
        inserted.append((asset_id, items))

    monkeypatch.setattr(catalog.store, "insert_items", AsyncMock(side_effect=_insert))
    monkeypatch.setattr(catalog.store, "delete_assets", AsyncMock())
    return inserted


async def test_sync_catalogues_only_assets_it_has_not_seen(monkeypatch):
    inserted = _patch_common(monkeypatch, catalogued={"asset-1"})
    monkeypatch.setattr(
        catalog,
        "invoke",
        _fake_invoke([{"id": "asset-1", "filename": "a.jpg"}, {"id": "asset-2", "filename": "b.jpg"}]),
    )
    monkeypatch.setattr(
        catalog.vision, "describe", AsyncMock(return_value=[_item("new shirt")])
    )

    result = await catalog.sync("sub-noah")

    assert result["catalogued"] == 1
    assert [asset for asset, _ in inserted] == ["asset-2"]


async def test_force_recatalogues_everything(monkeypatch):
    inserted = _patch_common(monkeypatch, catalogued={"asset-1"})
    monkeypatch.setattr(
        catalog, "invoke", _fake_invoke([{"id": "asset-1", "filename": "a.jpg"}])
    )
    monkeypatch.setattr(
        catalog.vision, "describe", AsyncMock(return_value=[_item("shirt")])
    )

    result = await catalog.sync("sub-noah", force=True)

    assert result["catalogued"] == 1
    assert [asset for asset, _ in inserted] == ["asset-1"]


async def test_one_photo_can_yield_several_garments(monkeypatch):
    inserted = _patch_common(monkeypatch)
    monkeypatch.setattr(
        catalog, "invoke", _fake_invoke([{"id": "asset-1", "filename": "rail.jpg"}])
    )
    monkeypatch.setattr(
        catalog.vision,
        "describe",
        AsyncMock(return_value=[_item("shirt a"), _item("shirt b"), _item("shirt c")]),
    )

    result = await catalog.sync("sub-noah")

    assert result["catalogued"] == 3
    assert [row["name"] for row in inserted[0][1]] == ["shirt a", "shirt b", "shirt c"]


async def test_assets_that_left_the_album_are_deleted(monkeypatch):
    _patch_common(monkeypatch, catalogued={"asset-1", "asset-gone"})
    monkeypatch.setattr(
        catalog, "invoke", _fake_invoke([{"id": "asset-1", "filename": "a.jpg"}])
    )
    monkeypatch.setattr(catalog.vision, "describe", AsyncMock(return_value=[]))

    result = await catalog.sync("sub-noah")

    assert result["removed"] == 1
    catalog.store.delete_assets.assert_awaited_once()
    assert catalog.store.delete_assets.await_args.args[1] == ["asset-gone"]


async def test_one_failing_photo_does_not_stop_the_others(monkeypatch):
    inserted = _patch_common(monkeypatch)
    monkeypatch.setattr(
        catalog,
        "invoke",
        _fake_invoke([{"id": "asset-1", "filename": "a.jpg"}, {"id": "asset-2", "filename": "b.jpg"}]),
    )

    calls = {"n": 0}

    async def _describe(image_base64, content_type):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the model hiccuped")
        return [_item("survivor")]

    monkeypatch.setattr(catalog.vision, "describe", _describe)

    result = await catalog.sync("sub-noah")

    assert result["failed"] == 1
    assert result["catalogued"] == 1
    assert [asset for asset, _ in inserted] == ["asset-2"]


async def test_a_limit_leaves_the_rest_for_next_time(monkeypatch):
    inserted = _patch_common(monkeypatch)
    monkeypatch.setattr(
        catalog,
        "invoke",
        _fake_invoke([{"id": f"asset-{n}", "filename": f"{n}.jpg"} for n in range(5)]),
    )
    monkeypatch.setattr(
        catalog.vision, "describe", AsyncMock(return_value=[_item("shirt")])
    )

    result = await catalog.sync("sub-noah", limit=2)

    assert result["catalogued"] == 2
    assert result["remaining"] == 3
    assert len(inserted) == 2


async def test_a_member_with_no_album_gets_a_clear_error(monkeypatch):
    monkeypatch.setattr(catalog, "album_for", lambda _sub: None)

    result = await catalog.sync("sub-noah")

    assert result["error"] == catalog.NO_ALBUM
    assert result["catalogued"] == 0


async def test_an_immich_failure_is_returned_not_raised(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        catalog, "invoke", AsyncMock(return_value="error: eve-tools unavailable")
    )

    result = await catalog.sync("sub-noah")

    assert result["error"].startswith("error:")
    assert result["catalogued"] == 0


async def test_render_groups_by_category_and_names_every_item(monkeypatch):
    monkeypatch.setattr(catalog, "album_for", lambda _sub: "album-1")
    monkeypatch.setattr(
        catalog.store,
        "list_items",
        AsyncMock(
            return_value=[
                {
                    "name": "navy wool blazer",
                    "category": "outerwear",
                    "attrs": {"warmth": 4, "formality": 4, "fabric": "wool"},
                },
                {
                    "name": "white oxford shirt",
                    "category": "top",
                    "attrs": {"warmth": 2, "formality": 3, "fabric": "cotton"},
                },
            ]
        ),
    )
    monkeypatch.setattr(
        catalog, "invoke", _fake_invoke([{"id": "a", "filename": "x"}, {"id": "b", "filename": "y"}])
    )

    rendered = await catalog.render_wardrobe("sub-noah")

    assert "outerwear" in rendered
    assert "navy wool blazer" in rendered
    assert "white oxford shirt" in rendered
    assert "warmth 4" in rendered


async def test_render_reports_an_empty_wardrobe(monkeypatch):
    monkeypatch.setattr(catalog, "album_for", lambda _sub: "album-1")
    monkeypatch.setattr(catalog.store, "list_items", AsyncMock(return_value=[]))
    monkeypatch.setattr(catalog, "invoke", _fake_invoke([]))

    assert await catalog.render_wardrobe("sub-noah") == catalog.EMPTY


async def test_render_flags_a_stale_catalogue(monkeypatch):
    monkeypatch.setattr(catalog, "album_for", lambda _sub: "album-1")
    monkeypatch.setattr(
        catalog.store,
        "list_items",
        AsyncMock(return_value=[{"name": "shirt", "category": "top", "attrs": {}}]),
    )
    monkeypatch.setattr(
        catalog,
        "invoke",
        _fake_invoke([{"id": "a", "filename": "x"}, {"id": "b", "filename": "y"}, {"id": "c", "filename": "z"}]),
    )
    monkeypatch.setattr(
        catalog.store, "catalogued_asset_ids", AsyncMock(return_value={"a"})
    )

    rendered = await catalog.render_wardrobe("sub-noah")

    assert "2 photo" in rendered
    assert "not been catalogued" in rendered


async def test_render_survives_immich_being_down(monkeypatch):
    monkeypatch.setattr(catalog, "album_for", lambda _sub: "album-1")
    monkeypatch.setattr(
        catalog.store,
        "list_items",
        AsyncMock(return_value=[{"name": "shirt", "category": "top", "attrs": {}}]),
    )
    monkeypatch.setattr(catalog, "invoke", AsyncMock(return_value="error: down"))

    rendered = await catalog.render_wardrobe("sub-noah")

    assert "shirt" in rendered
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_wardrobe_catalog.py -v`
Expected: FAIL — `ImportError: cannot import name 'catalog' from 'eve.wardrobe'`

- [ ] **Step 3: Write `src/eve/wardrobe/catalog.py`**

```python
"""The sync, and the one text rendering of a wardrobe.

`sync` is a batch job. It is the only place in the deployment that moves image
bytes, and it does so between eve-tools and `vision.describe` and nowhere
else - by the time anything here returns, the wardrobe is text.

`render_wardrobe` is the other half: the whole catalogue as one string for a
prompt. It lives here rather than in the stylist so it can be tested without
a model, and so the stylist's tool stays a two-line wrapper.
"""

from __future__ import annotations

import json
import logging

from eve.family import get_family
from eve.tools_client import invoke
from eve.wardrobe import store, vision

logger = logging.getLogger(__name__)

NO_ALBUM = (
    "No wardrobe album is configured for this member. Add a `wardrobe_album` "
    "to their entry in family.yaml with the id of their Immich album."
)
EMPTY = (
    "The wardrobe catalogue is empty. Photograph the clothes into the Immich "
    "album and run `eve-wardrobe sync` (or ask me to sync it)."
)

# ponytail: the whole wardrobe in one string, roughly 6k tokens at 200
# garments. Enumeration is the entire reason a catalogue beat a similarity
# search, so filtering here would defeat the point - but past ~300 items this
# stops being sensible and the tool grows a `category` argument.
_CATEGORY_ORDER = ("full", "outerwear", "top", "bottom", "footwear", "accessory")


def album_for(member_sub: str) -> str | None:
    """The member's Immich album id, from the roster. `None` when they have
    no wardrobe configured - not an error, just nothing to sync."""
    return get_family().get(member_sub).wardrobe_album


async def _album_asset_list(album_id: str) -> tuple[list[dict], str | None]:
    """`(assets, error)`. `invoke` returns a JSON string, or a string starting
    with `error:` - it never raises, which is why nothing here does either."""
    raw = await invoke("immich.album_assets", {"album_id": album_id})
    if raw.startswith("error:"):
        return [], raw
    try:
        return json.loads(raw).get("assets", []), None
    except (ValueError, AttributeError):
        return [], "error: Immich returned something that was not an album"


async def sync(
    member_sub: str, *, force: bool = False, limit: int | None = None
) -> dict:
    """Bring the catalogue in line with the album.

    `limit` bounds how many photographs one call will describe, so the
    conversational `sync_wardrobe` tool cannot spend a whole turn on a
    hundred-photo first run; `remaining` tells the caller what it left.
    """
    result = {"catalogued": 0, "removed": 0, "failed": 0, "remaining": 0, "error": None}

    album_id = album_for(member_sub)
    if not album_id:
        result["error"] = NO_ALBUM
        return result

    assets, error = await _album_asset_list(album_id)
    if error:
        result["error"] = error
        return result

    known = await store.catalogued_asset_ids(member_sub)
    in_album = {asset["id"] for asset in assets}

    departed = sorted(known - in_album)
    if departed:
        await store.delete_assets(member_sub, departed)
        result["removed"] = len(departed)

    todo = [a for a in assets if force or a["id"] not in known]
    if limit is not None and len(todo) > limit:
        result["remaining"] = len(todo) - limit
        todo = todo[:limit]

    for asset in todo:
        try:
            raw = await invoke("immich.asset_image", {"asset_id": asset["id"]})
            if raw.startswith("error:"):
                raise RuntimeError(raw)
            payload = json.loads(raw)
            items = await vision.describe(
                payload["base64"], payload.get("content_type", "image/jpeg")
            )
        except Exception:
            # One unreadable photograph must not cost the other ninety-nine.
            logger.warning(
                "could not catalogue asset %s for %s", asset["id"], member_sub,
                exc_info=True,
            )
            result["failed"] += 1
            continue
        await store.insert_items(
            member_sub, asset["id"], [vision.to_row(item) for item in items]
        )
        result["catalogued"] += len(items)

    return result


def _render_item(item: dict) -> str:
    attrs = item.get("attrs") or {}
    parts = [
        attrs.get("fabric"),
        attrs.get("pattern"),
        f"warmth {attrs['warmth']}" if attrs.get("warmth") else None,
        f"formality {attrs['formality']}" if attrs.get("formality") else None,
        attrs.get("season"),
        attrs.get("notes"),
    ]
    detail = ", ".join(p for p in parts if p)
    return f"- {item['name']}" + (f" ({detail})" if detail else "")


async def _staleness_note(member_sub: str, album_id: str) -> str:
    """One extra API call, no vision, no measurable latency - and the
    alternative is a stylist confidently dressing someone out of a wardrobe
    missing the coat they bought last week.

    Degrades to silence: if Immich cannot be reached, the catalogue we have is
    still worth answering from, and a failure to CHECK for staleness is not
    itself worth reporting to a member asking what to wear.
    """
    assets, error = await _album_asset_list(album_id)
    if error:
        return ""
    uncatalogued = len({a["id"] for a in assets} - await store.catalogued_asset_ids(member_sub))
    if not uncatalogued:
        return ""
    return (
        f"\n\nNote: {uncatalogued} photo(s) in the album have not been "
        "catalogued yet, so this list may be incomplete."
    )


async def render_wardrobe(member_sub: str) -> str:
    """The whole catalogue as one string, grouped by category."""
    album_id = album_for(member_sub)
    if not album_id:
        return NO_ALBUM

    items = await store.list_items(member_sub)
    if not items:
        return EMPTY

    by_category: dict[str, list[dict]] = {}
    for item in items:
        by_category.setdefault(item["category"], []).append(item)

    ordered = [c for c in _CATEGORY_ORDER if c in by_category]
    ordered += sorted(c for c in by_category if c not in _CATEGORY_ORDER)

    sections = [
        f"## {category}\n" + "\n".join(_render_item(i) for i in by_category[category])
        for category in ordered
    ]
    return "\n\n".join(sections) + await _staleness_note(member_sub, album_id)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_wardrobe_catalog.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add src/eve/wardrobe/catalog.py tests/test_wardrobe_catalog.py
git commit -m "feat(wardrobe): sync an Immich album into the catalogue, and render it"
```

---

### Task 6: The `eve-wardrobe` CLI

**Files:**
- Create: `src/eve/wardrobe/cli.py`
- Modify: `pyproject.toml` (`[project.scripts]`)
- Test: `tests/test_wardrobe_cli.py`

**Interfaces:**
- Consumes: `eve.wardrobe.catalog.sync`, `eve.wardrobe.catalog.render_wardrobe`, `eve.family.get_family`.
- Produces: console script `eve-wardrobe` → `eve.wardrobe.cli:main`, with subcommands `sync` (`--member`, `--force`) and `list` (`--member`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_wardrobe_cli.py`:

```python
"""tests/test_wardrobe_cli.py"""
from unittest.mock import AsyncMock

import pytest

from eve.family import Family, Member
from eve.wardrobe import cli


def _family():
    return Family(
        [
            Member("sub-noah", "Noah", "adult", "America/Vancouver", frozenset(), "album-n"),
            Member("sub-kendra", "Kendra", "adult", "America/Vancouver", frozenset(), None),
        ]
    )


async def test_targets_are_only_members_with_an_album(monkeypatch):
    monkeypatch.setattr(cli, "get_family", _family)
    assert [m.name for m in cli._targets(None)] == ["Noah"]


async def test_a_named_member_is_selected_case_insensitively(monkeypatch):
    monkeypatch.setattr(cli, "get_family", _family)
    assert [m.name for m in cli._targets("noah")] == ["Noah"]


async def test_an_unknown_member_name_raises(monkeypatch):
    monkeypatch.setattr(cli, "get_family", _family)
    with pytest.raises(SystemExit):
        cli._targets("nobody")


async def test_sync_reports_counts_per_member(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_family", _family)
    monkeypatch.setattr(
        cli.catalog,
        "sync",
        AsyncMock(
            return_value={
                "catalogued": 7,
                "removed": 1,
                "failed": 0,
                "remaining": 0,
                "error": None,
            }
        ),
    )

    await cli.run_sync(member=None, force=False)

    out = capsys.readouterr().out
    assert "Noah" in out
    assert "7" in out
    assert "removed 1" in out


async def test_sync_reports_an_error_without_crashing(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_family", _family)
    monkeypatch.setattr(
        cli.catalog,
        "sync",
        AsyncMock(
            return_value={
                "catalogued": 0,
                "removed": 0,
                "failed": 0,
                "remaining": 0,
                "error": "error: eve-tools unavailable",
            }
        ),
    )

    await cli.run_sync(member=None, force=False)

    assert "eve-tools unavailable" in capsys.readouterr().out


async def test_list_prints_the_rendered_wardrobe(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_family", _family)
    monkeypatch.setattr(
        cli.catalog, "render_wardrobe", AsyncMock(return_value="## top\n- white shirt")
    )

    await cli.run_list(member=None)

    assert "white shirt" in capsys.readouterr().out
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_wardrobe_cli.py -v`
Expected: FAIL — `ImportError: cannot import name 'cli' from 'eve.wardrobe'`

- [ ] **Step 3: Write `src/eve/wardrobe/cli.py`**

```python
"""`eve-wardrobe`: build and read the garment catalogue.

A CLI because the initial bulk load has to be one - a hundred photographs is
never going inside a conversational turn - and because the operator is one
person with a terminal. The stylist's `sync_wardrobe` tool calls the same
`catalog.sync`, bounded by a limit; this is the unbounded caller.

Modelled on `eve-skill` and `eve-tool`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from eve.family import Member, get_family
from eve.memory.db import close_pool
from eve.wardrobe import catalog


def _targets(member: str | None) -> list[Member]:
    """Who to sync. Without `--member`, every member who actually has an
    album - syncing a member with no wardrobe is not an error worth printing
    once per run."""
    members = get_family().members()
    if member is None:
        return [m for m in members if m.wardrobe_album]
    matched = [m for m in members if m.name.lower() == member.lower()]
    if not matched:
        names = ", ".join(m.name for m in members)
        raise SystemExit(f"no family member named {member!r} (have: {names})")
    return matched


async def run_sync(member: str | None, force: bool) -> None:
    for target in _targets(member):
        result = await catalog.sync(target.sub, force=force)
        if result["error"]:
            print(f"{target.name}: {result['error']}")
            continue
        line = (
            f"{target.name}: catalogued {result['catalogued']} garment(s), "
            f"removed {result['removed']}, failed {result['failed']}"
        )
        if result["remaining"]:
            line += f", {result['remaining']} left for the next run"
        print(line)


async def run_list(member: str | None) -> None:
    for target in _targets(member):
        print(f"# {target.name}")
        print(await catalog.render_wardrobe(target.sub))
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    syncer = sub.add_parser("sync", help="catalogue new photos from the Immich album")
    syncer.add_argument("--member", default=None, help="one member by name")
    syncer.add_argument(
        "--force",
        action="store_true",
        help="re-describe every photo, not just new ones (use after editing prompts/wardrobe.md)",
    )

    lister = sub.add_parser("list", help="print the catalogue")
    lister.add_argument("--member", default=None, help="one member by name")

    args = parser.parse_args()

    async def _run() -> None:
        try:
            if args.command == "sync":
                await run_sync(args.member, args.force)
            else:
                await run_list(args.member)
        finally:
            await close_pool()

    try:
        asyncio.run(_run())
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        raise
```

- [ ] **Step 4: Register the console script**

In `pyproject.toml`, under `[project.scripts]`, keeping alphabetical order:

```toml
eve-tool = "eve.tools_authoring.cli:main"
eve-wardrobe = "eve.wardrobe.cli:main"
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_wardrobe_cli.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Verify the entry point resolves**

Run: `uv sync && uv run eve-wardrobe --help`
Expected: argparse help listing `sync` and `list`.

- [ ] **Step 7: Commit**

```bash
git add src/eve/wardrobe/cli.py pyproject.toml tests/test_wardrobe_cli.py
git commit -m "feat(wardrobe): add the eve-wardrobe CLI"
```

---

### Task 7: Skills gain a `specialist` scope, and Eve stops seeing them

**Files:**
- Modify: `src/eve/skills/registry.py` (`parse_skill_text`, `Skill`, `_load_skill_md`, `load_skills`)
- Modify: `src/eve/skills/search.py` (filter Eve's corpus)
- Test: `tests/test_skills_registry.py`
- Test: `tests/test_skills_search.py`

**Interfaces:**
- Produces:
  - `parse_skill_text(text: str, fallback_name: str) -> tuple[str, str, str, str | None]` — now a **four**-tuple: `(name, description, body, specialist)`.
  - `Skill` dataclass gains `specialist: str | None = None`.

**Note for the implementer:** `parse_skill_text`'s arity changes. Both callers are in `registry.py`; update them in the same step, and check `tests/test_skills_registry.py` for any existing three-tuple unpacking.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_skills_registry.py`:

```python
def test_a_skill_without_a_specialist_key_belongs_to_eve():
    from eve.skills.registry import parse_skill_text

    name, description, body, specialist = parse_skill_text(
        "---\nname: greet\ndescription: how to greet\n---\nUse their name.", "fallback"
    )

    assert name == "greet"
    assert description == "how to greet"
    assert body == "Use their name."
    assert specialist is None


def test_a_specialist_key_is_parsed():
    from eve.skills.registry import parse_skill_text

    _, _, _, specialist = parse_skill_text(
        "---\nname: dress\ndescription: d\nspecialist: stylist\n---\nBody.", "fallback"
    )

    assert specialist == "stylist"


def test_load_skills_carries_the_specialist_through(tmp_path, monkeypatch):
    from eve.settings import get_settings
    from eve.skills.registry import load_skills

    (tmp_path / "dress-for-the-day").mkdir()
    (tmp_path / "dress-for-the-day" / "SKILL.md").write_text(
        "---\nname: dress-for-the-day\ndescription: outfits\nspecialist: stylist\n---\nBody."
    )
    (tmp_path / "greet-warmly").mkdir()
    (tmp_path / "greet-warmly" / "SKILL.md").write_text(
        "---\nname: greet-warmly\ndescription: greeting\n---\nBody."
    )
    monkeypatch.setenv("EVE_SKILLS_DIR", str(tmp_path))
    get_settings.cache_clear()

    by_name = {s.name: s for s in load_skills()}

    assert by_name["dress-for-the-day"].specialist == "stylist"
    assert by_name["greet-warmly"].specialist is None
```

Add to `tests/test_skills_search.py`:

```python
async def test_eve_never_sees_a_specialist_scoped_skill(tmp_path, monkeypatch):
    """A scoped skill must not reach Eve's own search - she delegates rather
    than dresses, and a procedure she cannot act on is noise in her context."""
    from eve.settings import get_settings
    from eve.skills import search as search_module

    (tmp_path / "dress-for-the-day").mkdir()
    (tmp_path / "dress-for-the-day" / "SKILL.md").write_text(
        "---\nname: dress-for-the-day\ndescription: how to assemble an outfit\n"
        "specialist: stylist\n---\nAnchor on one item."
    )
    monkeypatch.setenv("EVE_SKILLS_DIR", str(tmp_path))
    get_settings.cache_clear()
    monkeypatch.setattr(
        search_module, "embed_query", AsyncMock(return_value=[1.0, 0.0, 0.0])
    )

    result = await search_module.search_skills.ainvoke(
        {
            "query": "what should I wear",
            "state": STATE,
            "tool_call_id": "call-1",
        }
    )

    message = result.update["messages"][0]
    assert "dress-for-the-day" not in message.content
    assert "Anchor on one item" not in message.content
```

The implementer should reuse whatever `STATE` fixture and `AsyncMock` import `tests/test_skills_search.py` already defines; if the file's existing tests build state inline, follow that shape instead.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_skills_registry.py tests/test_skills_search.py -v`
Expected: FAIL — `ValueError: not enough values to unpack (expected 4, got 3)`

- [ ] **Step 3: Add the field to the parser and the dataclass**

In `src/eve/skills/registry.py`, change the `Skill` dataclass:

```python
@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    kind: str  # "procedure" | "mcp_tool"
    content: str
    spec: DynamicToolSpec | None = None
    # None means the skill is Eve's own. A name means it belongs to that
    # specialist alone: Eve's search filters it out, and that specialist's
    # search filters everything else out (design doc, "Scoped skills").
    specialist: str | None = None
```

Change `parse_skill_text`'s docstring and return:

```python
def parse_skill_text(text: str, fallback_name: str) -> tuple[str, str, str, str | None]:
    """Split a SKILL.md-shaped document into
    (name, description, body, specialist).

    One parser for two sources: files on disk and Eve-authored procedure rows,
    which serialize to this same shape (eve.skills.authoring). `eve_memory`
    has no description column, and reusing this format is cheaper than adding
    one.
    """
    if text.startswith("---"):
        _, frontmatter, body = text.split("---", 2)
        meta = yaml.safe_load(frontmatter) or {}
    else:
        meta, body = {}, text
    return (
        meta.get("name", fallback_name),
        meta.get("description", ""),
        body.strip(),
        meta.get("specialist") or None,
    )
```

Update `_load_skill_md`:

```python
def _load_skill_md(path) -> Skill:
    name, description, body, specialist = parse_skill_text(
        path.read_text(), path.parent.name
    )
    return Skill(
        name=name,
        description=description,
        kind="procedure",
        content=body,
        specialist=specialist,
    )
```

Update the authored-row branch of `load_skills`:

```python
    for row in authored or []:
        name, description, body, specialist = parse_skill_text(
            row.content, row.subject or str(row.id)
        )
        procedures.append(
            Skill(
                name=name,
                description=description,
                kind="procedure",
                content=body,
                specialist=specialist,
            )
        )
```

- [ ] **Step 4: Filter Eve's corpus**

In `src/eve/skills/search.py`, replace the `skills = load_skills(...)` line with:

```python
    # Eve sees only unscoped skills. A specialist-scoped procedure is reachable
    # exclusively through that specialist's own search
    # (eve.skills.specialist_search) - she delegates rather than acts on it,
    # so it would be noise in her context and a procedure she cannot follow.
    skills = [
        skill
        for skill in load_skills(
            mcp_tools=[*registered_mcp_tools(), *sandbox], authored=authored
        )
        if skill.specialist is None
    ]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_skills_registry.py tests/test_skills_search.py tests/test_skills_authoring.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/eve/skills/registry.py src/eve/skills/search.py \
        tests/test_skills_registry.py tests/test_skills_search.py
git commit -m "feat(skills): scope a skill to one specialist, and hide it from Eve"
```

---

### Task 8: A specialist's own skills search

**Files:**
- Create: `src/eve/skills/specialist_search.py`
- Modify: `src/eve/specialists/base.py` (append the tool in `build_specialist`)
- Test: `tests/test_skills_specialist_search.py`
- Test: `tests/test_specialists_base.py` (one added test)

**Interfaces:**
- Consumes: `eve.skills.registry.load_skills`, `eve.skills.search.rank_skills`.
- Produces: `def build_skills_search(specialist: str) -> BaseTool` — a tool named `search_skills` returning a plain string.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_skills_specialist_search.py`:

```python
"""tests/test_skills_specialist_search.py"""
from unittest.mock import AsyncMock

from eve.skills import specialist_search


def _write_skill(root, folder, name, description, specialist=None):
    (root / folder).mkdir()
    lines = ["---", f"name: {name}", f"description: {description}"]
    if specialist:
        lines.append(f"specialist: {specialist}")
    lines += ["---", f"Body of {name}."]
    (root / folder / "SKILL.md").write_text("\n".join(lines))


def _skills_dir(tmp_path, monkeypatch):
    from eve.settings import get_settings

    monkeypatch.setenv("EVE_SKILLS_DIR", str(tmp_path))
    get_settings.cache_clear()


async def test_a_specialist_sees_only_its_own_skills(tmp_path, monkeypatch):
    _write_skill(tmp_path, "dress", "dress-for-the-day", "outfits", "stylist")
    _write_skill(tmp_path, "greet", "greet-warmly", "greeting")
    _write_skill(tmp_path, "triage", "triage-mail", "inbox", "mail")
    _skills_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(
        specialist_search, "embed_query", AsyncMock(return_value=[1.0, 0.0])
    )

    tool = specialist_search.build_skills_search("stylist")
    result = await tool.ainvoke({"query": "what should I wear"})

    assert "dress-for-the-day" in result
    assert "Body of dress-for-the-day." in result
    assert "greet-warmly" not in result
    assert "triage-mail" not in result


async def test_a_specialist_with_no_skills_gets_a_clean_answer(tmp_path, monkeypatch):
    _write_skill(tmp_path, "greet", "greet-warmly", "greeting")
    _skills_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(
        specialist_search, "embed_query", AsyncMock(return_value=[1.0, 0.0])
    )

    tool = specialist_search.build_skills_search("finances")
    result = await tool.ainvoke({"query": "anything"})

    assert result == specialist_search.NO_MATCH


async def test_the_tool_returns_a_string_not_a_command(tmp_path, monkeypatch):
    """A specialist's loop is create_agent's own message state, not EveState -
    there is no dynamic_tools channel to update and no rebinding step to
    receive a spec, so this side is knowledge only."""
    _write_skill(tmp_path, "dress", "dress-for-the-day", "outfits", "stylist")
    _skills_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(
        specialist_search, "embed_query", AsyncMock(return_value=[1.0, 0.0])
    )

    tool = specialist_search.build_skills_search("stylist")
    result = await tool.ainvoke({"query": "outfit"})

    assert isinstance(result, str)
```

Add to `tests/test_specialists_base.py`:

```python
async def test_every_specialist_gets_a_scoped_skills_search(monkeypatch):
    captured = {}

    def _fake_create_agent(model, tools, system_prompt):
        captured["tools"] = tools
        return _AGENT_STUB

    monkeypatch.setattr("eve.specialists.base.create_agent", _fake_create_agent)

    specialist = build_specialist(
        name="widgets",
        tools=[get_widget],
        system_prompt="You handle widgets.",
        permission="home.control",
        model_factory=_factory_with(AIMessage(content="done")),
    )
    await specialist.ainvoke({"request": "hello", "state": STATE}, config=CONFIG)

    assert [t.name for t in captured["tools"]] == ["get_widget", "search_skills"]
```

The implementer will need an `_AGENT_STUB` whose `ainvoke` returns
`{"messages": [AIMessage(content="done")]}`; define it near the top of the
file if one does not already exist:

```python
class _AgentStub:
    async def ainvoke(self, _payload, _config):
        return {"messages": [AIMessage(content="done")]}


_AGENT_STUB = _AgentStub()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_skills_specialist_search.py tests/test_specialists_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve.skills.specialist_search'`

- [ ] **Step 3: Write `src/eve/skills/specialist_search.py`**

```python
"""A specialist's own skills search: its scoped procedures, as text.

Deliberately NOT `eve.skills.search.search_skills`. That tool returns a
`Command` because half its job is appending DynamicToolSpecs to
`dynamic_tools` in EveState for materialization on the next model call. Inside
a specialist the loop is `create_agent`'s own message state, not EveState, and
there is no rebinding step to receive a spec - so MCP, sandbox and authored
matches are all excluded here and this is a plain string tool.

Knowledge crosses the boundary; capability does not. If a specialist ever
needs a dynamically-bound tool, that is a design problem deserving its own
ticket rather than something smuggled in behind a skills search.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool
from opentelemetry import trace

from eve.skills.registry import load_skills
from eve.skills.search import rank_skills

NO_MATCH = "No matching skill found."


def build_skills_search(specialist: str) -> BaseTool:
    """One scoped search tool, appended to every specialist by
    `build_specialist`. A specialist with no skills of its own searches an
    empty set and gets NO_MATCH, which is the correct answer."""

    async def search_skills(query: str) -> str:
        # ponytail: filesystem corpus only - no Eve-authored database rows,
        # which would mean a Postgres round trip inside every specialist loop.
        # Add one if authored specialist procedures ever become a real want.
        skills = [s for s in load_skills() if s.specialist == specialist]
        matches = await rank_skills(query, skills)
        trace.get_current_span().set_attribute(
            "eve.skills.specialist_search_used", specialist
        )
        if not matches:
            return NO_MATCH
        return "\n\n".join(f"# {m.name}\n{m.content}" for m in matches)

    search_skills.__doc__ = (
        f"Search the {specialist} specialist's own procedures for guidance on "
        "how to handle a request. Returns written guidance, not a new tool."
    )
    return tool(search_skills)
```

**Where to patch the embedder.** `rank_skills` lives in `eve.skills.search`
and calls the `embed_query` bound in *that* module, so patching
`specialist_search.embed_query` would have no effect. Every test in Step 1
must patch it at its real home instead — go back and change each
`monkeypatch.setattr(specialist_search, "embed_query", ...)` to:

```python
monkeypatch.setattr("eve.skills.search.embed_query", AsyncMock(return_value=[1.0, 0.0]))
```

with `from unittest.mock import AsyncMock` at the top of the test file. If the
tests pass while still patching the wrong module, they are passing for the
wrong reason — a real embedding call is being made.

- [ ] **Step 4: Append the tool in `build_specialist`**

In `src/eve/specialists/base.py`, add the import:

```python
from eve.skills.specialist_search import build_skills_search
```

and inside `ask`, change the agent construction:

```python
        if "agent" not in agent_holder:
            agent_holder["agent"] = create_agent(
                model_factory(Tier.MECHANICAL),
                # Every specialist gets its own scoped skills search from the
                # factory rather than from four separate wirings, so the
                # capability arrives with the mechanism. A specialist with no
                # skills searches an empty set and is told so.
                [*tools, build_skills_search(name)],
                system_prompt=SystemMessage(
                    system_prompt, additional_kwargs=_OPENAI_DEVELOPER_ROLE
                ),
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_skills_specialist_search.py tests/test_specialists_base.py tests/test_specialists_home.py tests/test_specialists_mail.py tests/test_specialists_finances.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/eve/skills/specialist_search.py src/eve/specialists/base.py \
        tests/test_skills_specialist_search.py tests/test_specialists_base.py
git commit -m "feat(skills): give every specialist a scoped skills search"
```

---

### Task 9: The stylist specialist

**Files:**
- Create: `prompts/stylist.md`
- Create: `skills/dress-for-the-day/SKILL.md`
- Create: `src/eve/specialists/stylist.py`
- Test: `tests/test_specialists_stylist.py`

**Interfaces:**
- Consumes: `eve.wardrobe.catalog.render_wardrobe`, `eve.wardrobe.catalog.sync`, `eve.tools_client.invoke`, `eve.specialists.base.build_specialist`, `eve.specialists.permissions.permission_denial`.
- Produces: `ask_stylist` (a `BaseTool`), plus the module-level tools `read_wardrobe`, `todays_weather`, `list_events`, `sync_wardrobe`, and `_model_for_test`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_specialists_stylist.py`:

```python
"""tests/test_specialists_stylist.py"""
import importlib
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage

import eve.specialists.stylist as stylist_module
from tests.conftest import FakeToolCallingModel
from tests.test_specialists_base import MEMBER, STATE

CONFIG = {
    "configurable": {
        "member": {
            "sub": "sub-noah",
            "permissions": ["wardrobe", "calendar.read"],
        }
    }
}


async def test_read_wardrobe_returns_the_rendered_catalogue(monkeypatch):
    monkeypatch.setattr(
        stylist_module.catalog,
        "render_wardrobe",
        AsyncMock(return_value="## top\n- white oxford shirt"),
    )

    result = await stylist_module.read_wardrobe.ainvoke({}, config=CONFIG)

    assert "white oxford shirt" in result
    stylist_module.catalog.render_wardrobe.assert_awaited_once_with("sub-noah")


async def test_todays_weather_relays_home_assistant(monkeypatch):
    monkeypatch.setattr(
        stylist_module, "invoke", AsyncMock(return_value='{"temperature": 8}')
    )

    result = await stylist_module.todays_weather.ainvoke({}, config=CONFIG)

    assert "8" in result
    stylist_module.invoke.assert_awaited_once_with("home.weather", {})


async def test_list_events_requires_calendar_read(monkeypatch):
    invoke = AsyncMock()
    monkeypatch.setattr(stylist_module, "invoke", invoke)
    config = {"configurable": {"member": {"sub": "sub-noah", "permissions": ["wardrobe"]}}}

    result = await stylist_module.list_events.ainvoke({}, config=config)

    assert "Permission denied" in result
    assert "calendar.read" in result
    invoke.assert_not_awaited()


async def test_list_events_passes_the_member_sub(monkeypatch):
    monkeypatch.setattr(stylist_module, "invoke", AsyncMock(return_value="[]"))

    await stylist_module.list_events.ainvoke({}, config=CONFIG)

    stylist_module.invoke.assert_awaited_once_with(
        "calendar.list_events",
        {"member_sub": "sub-noah", "lookahead_minutes": 960, "horizon_days": 1},
    )


async def test_sync_wardrobe_is_bounded_and_reports_what_is_left(monkeypatch):
    monkeypatch.setattr(
        stylist_module.catalog,
        "sync",
        AsyncMock(
            return_value={
                "catalogued": 5,
                "removed": 0,
                "failed": 0,
                "remaining": 12,
                "error": None,
            }
        ),
    )

    result = await stylist_module.sync_wardrobe.ainvoke({}, config=CONFIG)

    assert "5" in result
    assert "12" in result
    kwargs = stylist_module.catalog.sync.await_args.kwargs
    assert kwargs["limit"] == stylist_module.SYNC_LIMIT


async def test_sync_wardrobe_surfaces_an_error(monkeypatch):
    monkeypatch.setattr(
        stylist_module.catalog,
        "sync",
        AsyncMock(
            return_value={
                "catalogued": 0,
                "removed": 0,
                "failed": 0,
                "remaining": 0,
                "error": "error: eve-tools unavailable",
            }
        ),
    )

    result = await stylist_module.sync_wardrobe.ainvoke({}, config=CONFIG)

    assert result.startswith("error:")


async def test_a_member_without_the_wardrobe_permission_is_denied():
    state = {**STATE, "member": {**MEMBER, "permissions": ["home.control"]}}

    result = await stylist_module.ask_stylist.ainvoke(
        {"request": "what should I wear", "state": state},
        config={"configurable": {}},
    )

    assert "Permission denied" in result
    assert "wardrobe" in result


async def test_the_stylist_reads_the_wardrobe_through_its_loop(monkeypatch):
    tool_call = {
        "name": "read_wardrobe",
        "args": {},
        "id": "call-1",
        "type": "tool_call",
    }
    monkeypatch.setattr(
        "eve.specialists.stylist._model_for_test",
        lambda: FakeToolCallingModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[tool_call]),
                    AIMessage(content="Wear the navy wool blazer."),
                ]
            )
        ),
    )
    importlib.reload(stylist_module)
    monkeypatch.setattr(
        stylist_module.catalog,
        "render_wardrobe",
        AsyncMock(return_value="## outerwear\n- navy wool blazer"),
    )

    state = {**STATE, "member": {**MEMBER, "permissions": ["wardrobe"]}}
    result = await stylist_module.ask_stylist.ainvoke(
        {"request": "what should I wear", "state": state},
        config={"configurable": {}},
    )

    assert "navy wool blazer" in result
```

**Note:** `importlib.reload` in the last test rebuilds `ask_stylist` against
the fake model, following `tests/test_specialists_home.py`. Reload leaves the
module object replaced, so the `monkeypatch.setattr` on `catalog` must come
*after* the reload, as written.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_specialists_stylist.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve.specialists.stylist'`

- [ ] **Step 3: Write the system prompt**

Create `prompts/stylist.md`:

```markdown
You are the family's stylist. A member asks what to wear; you answer from the
clothes they actually own.

You do not know what is in their wardrobe. Call `read_wardrobe` first, every
time, before recommending anything — the catalogue changes between requests
and nothing you remember from an earlier one is reliable.

Unless the member has already told you the occasion and the conditions, call
`todays_weather` and `list_events` too. What is on the calendar sets how
formal the day has to be; the forecast sets how warm.

**Never name a garment that was not in the catalogue you just read.** The
member cannot see a photograph of what you are describing — they will go to
the wardrobe and look for it. Recommending something they do not own is the
one failure that makes you worse than useless. If the wardrobe cannot cover
the day, say so plainly and suggest the closest thing it can do.

Call `search_skills` when you want the household's written guidance on how to
put an outfit together.

Answer with one recommendation, naming each garment exactly as the catalogue
names it, and one short sentence saying why it suits the day. Add at most one
alternative. Do not list the wardrobe back to them, do not explain your
process, and do not hedge across four options — they asked what to wear.

If the catalogue is empty or stale, tell them, and say what to do about it.
```

- [ ] **Step 4: Write the skill**

Create `skills/dress-for-the-day/SKILL.md`:

```markdown
---
name: dress-for-the-day
description: How to assemble an outfit for a specific day from a catalogued wardrobe, given the weather and what is on the calendar.
specialist: stylist
---
A starting set of heuristics, not a theory of dress. Edit freely.

**Formality is set by the most formal thing on the calendar**, not by the
average of the day. One client meeting at two o'clock governs the whole
outfit; there is no changing at lunchtime.

**Dress for the day's coldest relevant moment**, not its midpoint. A forecast
high of 14°C with a 7 a.m. start and a 9 p.m. finish is a 7°C day. Layering is
how a wardrobe covers a range: a mid-weight layer that can come off beats a
heavy one that cannot.

**Anchor on one item and build outward.** Pick the piece the day most requires
— the warm coat, the formal trousers, the boots that suit the rain — and
choose everything else to work with it. Assembling from the top down without
an anchor produces outfits that are individually fine and collectively wrong.

**One statement piece.** A bold pattern, a saturated colour, or an unusual
silhouette earns its place only if everything else is quiet. Two compete.

**Colour:** keep to two or three across the whole outfit. Navy, grey, olive,
brown and cream go with nearly everything in a normal wardrobe and with each
other. Match the belt to the shoes when both are leather.

**Warmth ratings compose roughly additively.** Two warmth-2 layers cover about
what one warmth-3 layer does, with the advantage that it can be taken apart.

**Check the feet against the ground, not the sky.** Rain that stopped an hour
ago still means wet pavements.

**When the wardrobe genuinely cannot cover the day**, say which piece is
missing rather than recommending the least-bad substitute silently. That gap
is worth knowing about.
```

- [ ] **Step 5: Write `src/eve/specialists/stylist.py`**

```python
"""Stylist specialist: what to wear today, from the clothes the member owns.

The first specialist whose subject is a set of objects rather than a service
API. The objects are photographs in an Immich album, catalogued into
`eve_wardrobe_item` by `eve.wardrobe.catalog` - so every tool here reads text
and no image ever enters this loop (design doc, "Eve cannot show you a
photograph" and "How Eve perceives a wardrobe").

Permission is checked twice, the pattern `mail.py` established: the coarse
`wardrobe` grant at the Eve -> stylist edge, and the fine `calendar.read`
grant inside `list_events`.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from eve.models import Tier, get_model
from eve.specialists.base import build_specialist
from eve.specialists.permissions import permission_denial
from eve.tools_client import invoke
from eve.wardrobe import catalog

SYSTEM_PROMPT = Path("prompts/stylist.md").read_text()

# ponytail: a flat cap on one conversational sync, so "I added some clothes"
# cannot spend a whole turn describing a hundred photographs. The CLI is the
# unbounded caller; this one reports what it left behind.
SYNC_LIMIT = 10

# The rest of today, roughly. The calendar handler takes minutes-ahead and a
# day horizon; a stylist cares about what is left of this day, not a fortnight.
_LOOKAHEAD_MINUTES = 960
_HORIZON_DAYS = 1


def _model_for_test():
    """Indirection so unit tests can substitute a fake model, via
    importlib.reload, without a live LiteLLM call at import time."""
    return get_model(Tier.MECHANICAL)


def _member(config: RunnableConfig) -> dict:
    return config["configurable"]["member"]


@tool
async def read_wardrobe(config: RunnableConfig) -> str:
    """Read the member's whole wardrobe catalogue, grouped by category. Call
    this before recommending anything: it is the only list of clothes they
    actually own, and it changes between requests."""
    return await catalog.render_wardrobe(_member(config)["sub"])


@tool
async def todays_weather(config: RunnableConfig) -> str:
    """Today's forecast for the household."""
    return await invoke("home.weather", {})


@tool
async def list_events(config: RunnableConfig) -> str:
    """What is on the member's calendar for the rest of today. Requires the
    calendar.read permission."""
    member = _member(config)
    denial = permission_denial(member.get("permissions", []), "calendar.read")
    if denial:
        return denial
    return await invoke(
        "calendar.list_events",
        {
            "member_sub": member["sub"],
            "lookahead_minutes": _LOOKAHEAD_MINUTES,
            "horizon_days": _HORIZON_DAYS,
        },
    )


@tool
async def sync_wardrobe(config: RunnableConfig) -> str:
    """Catalogue any new photos the member has added to their Immich wardrobe
    album. Use when they say they have added or removed clothes, or when the
    catalogue reports itself stale."""
    result = await catalog.sync(_member(config)["sub"], limit=SYNC_LIMIT)
    if result["error"]:
        return result["error"]
    parts = [f"Catalogued {result['catalogued']} new garment(s)"]
    if result["removed"]:
        parts.append(f"removed {result['removed']} no longer in the album")
    if result["failed"]:
        parts.append(f"{result['failed']} photo(s) could not be read")
    if result["remaining"]:
        parts.append(
            f"{result['remaining']} photo(s) still uncatalogued - sync again to finish"
        )
    return ", ".join(parts) + "."


ask_stylist = build_specialist(
    name="stylist",
    tools=[read_wardrobe, todays_weather, list_events, sync_wardrobe],
    system_prompt=SYSTEM_PROMPT,
    permission="wardrobe",
    model_factory=lambda _tier: _model_for_test(),
)
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/test_specialists_stylist.py -v`
Expected: PASS (8 tests)

- [ ] **Step 7: Commit**

```bash
git add prompts/stylist.md skills/dress-for-the-day/SKILL.md \
        src/eve/specialists/stylist.py tests/test_specialists_stylist.py
git commit -m "feat(stylist): add the stylist specialist"
```

---

### Task 10: Wire the stylist into Eve, and grant the permission

**Files:**
- Modify: `src/eve/graph.py` (import and `_BASE_TOOLS`)
- Modify: `family.yaml` (the `wardrobe` grant for both adults)
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `ask_stylist` from Task 9.
- Produces: `ask_stylist` present in `_BASE_TOOLS`, so it is bound on every turn.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_graph.py`:

```python
def test_the_stylist_is_bound_on_every_turn():
    from eve.graph import _static_tools

    assert "ask_stylist" in [t.name for t in _static_tools()]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_graph.py -v -k stylist`
Expected: FAIL — `AssertionError`

- [ ] **Step 3: Wire it into the graph**

In `src/eve/graph.py`, add the import beside the other specialists (alphabetical):

```python
from eve.specialists.mail import ask_mail
from eve.specialists.stylist import ask_stylist
```

and add it to `_BASE_TOOLS`:

```python
_BASE_TOOLS = [
    ask_home,
    ask_mail,
    ask_finances,
    ask_stylist,
    search_skills,
    search_memory,
]
```

The stylist is unconditional, not behind a settings flag: unlike the sandbox,
computer and self-authoring tools, it grants no new class of action — a member
without the `wardrobe` permission is refused at the edge, which is the switch.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_graph.py -v`
Expected: PASS

- [ ] **Step 5: Grant the permission**

In `family.yaml`, in Noah's `permissions` list:

```yaml
      # The stylist: reading the wardrobe catalogue and asking what to wear.
      - wardrobe
```

and the same for Kendra.

- [ ] **Step 6: Run the whole unit suite**

Run: `uv run pytest -v -m "not integration and not live"`
Expected: PASS, no regressions.

- [ ] **Step 7: Run the integration suite**

Run: `docker compose -f docker-compose.test.yml up -d && uv run pytest -v -m integration`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/eve/graph.py family.yaml tests/test_graph.py
git commit -m "feat(stylist): bind the stylist on Eve's loop and grant the permission"
```

---

## Manual Verification

After Task 10, before opening a pull request. These are the spec's definition
of done, and none of them is covered by an automated test.

- [ ] **Fill in the album ids.** Create a wardrobe album in Immich, photograph
      a dozen garments into it, and put its id in `family.yaml`'s
      `wardrobe_album` for Noah.
- [ ] **Bulk catalogue.** `uv run eve-wardrobe sync` — confirm the count
      matches the number of photographs, allowing for multi-garment shots.
- [ ] **Read it back.** `uv run eve-wardrobe list` — confirm every name is one
      you would recognise if Eve said it aloud. If they are not, retune
      `prompts/wardrobe.md` and re-run with `--force`. **This is the single
      highest-value manual step**; every downstream answer is only as good as
      these names.
- [ ] **Ask her.** On a real day, ask Eve what to wear. Confirm the outfit
      names real garments, and that the forecast and a real calendar entry
      visibly informed it.
- [ ] **Check the honesty case.** Ask on a day the wardrobe genuinely cannot
      cover (or temporarily empty the album) and confirm she says so rather
      than inventing a coat.
- [ ] **Check the permission edge.** Temporarily remove `wardrobe` from a
      member and confirm the refusal; remove only `calendar.read` and confirm
      she still dresses you on weather alone.
- [ ] **Check the scope.** Ask Eve directly for a skill about outfits and
      confirm `dress-for-the-day` does not come back — it belongs to the
      stylist.
- [ ] **Add and remove.** Add one garment to the album, sync, confirm it can
      be recommended. Remove one, sync, confirm it stops appearing.

---

## Self-Review Notes

Checked against the spec, section by section:

- "Eve cannot show you a photograph" → Task 9's prompt and the never-invent
  rule; no image component anywhere.
- "How Eve perceives a wardrobe" → Tasks 1, 2, 4, 5. Pixels confined to
  `catalog.sync` ↔ `vision.describe`.
- "Architecture" → Tasks 1 (eve-tools), 2/4/5/6 (`eve/wardrobe/`), 9
  (stylist), 10 (family.yaml, graph.py).
- "The calendar" → Task 9's `list_events`, with the double permission check.
- "The garment record" → Task 2's migration, including `item_index` and jsonb
  `attrs`; `wardrobe_album` in Task 3.
- "The vision pass" → Task 4, on `Tier.REFLEX`, prompt in `prompts/wardrobe.md`.
- "Sync" → Task 5 (`force`, idempotence, deletion, staleness) and Task 6 (CLI)
  and Task 9 (`sync_wardrobe`). The ambient-tick rejection needs no task.
- "Scoped skills" → Tasks 7 and 8.
- "Behaviour" → Task 9's prompt and skill.
- "Failure and degradation" → covered by tests in Tasks 5 and 9; the
  loop-exhausted case is pre-existing in `base.py` and needs no new work.
- "Known ceilings" → `ponytail:` comments in Tasks 1, 5, 8, 9.
- "Definition of done" → the Manual Verification checklist above.

**Two gaps the spec names that this plan deliberately does not close:**

1. **Observability.** The spec asks for `eve.wardrobe.items_catalogued`,
   `eve.wardrobe.vision_failures`, `eve.wardrobe.stale_count` and
   `eve.skills.specialist_search_used`. Only the last is implemented (Task 8).
   The wardrobe three belong on a sync, which is a CLI batch job outside any
   trace — emitting them needs a span the CLI does not currently open, and
   `sync`'s return dict already carries the same numbers to the only two
   callers. **If you want them, add a Task 11 that opens a span in
   `catalog.sync`;** otherwise treat the returned counts as the answer and
   amend the spec.
2. **The live Immich test.** The spec's testing section asks for one real call
   to catch the album response shape flagged as unverified. That belongs with
   the other `test_*_live.py` files, needs a real instance and album id, and
   is better written by whoever first points this at a live Immich. Until then
   the album shape in Task 1 is an assumption, and Manual Verification's first
   two steps are what catch it being wrong.
