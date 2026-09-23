# Images Through Eve Implementation Plan (EVE-21)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let images move through Eve both ways. Stored photos (Immich) reach the phone as a new `image` surface component, and photos the member sends reach Eve's VOICE model and opted-in specialists. No pixel ever enters `EveState`, a checkpoint, a tool argument, or a UI frame.

**Architecture:** Images are rows in a new Postgres table, `eve_image`, addressed by an opaque UUID. They are re-encoded on write and expire by policy. Everything else carries only a `{"type": "eve_image", "image_id", "alt"}` reference block, or an `imageId` property on a surface component. Pixels appear in exactly three places:
- `hydrate`, which turns references into native image blocks (or cached REFLEX captions) immediately before a model call.
- `GET /images/{id}`, an owner-only proxy the phone fetches with its existing bearer token.
- The Immich cache fill in `from_immich`.

**Tech Stack:**
- Python 3.12, LangGraph/Aegra, langchain 1.3 (`create_agent`, `wrap_model_call` middleware), psycopg 3, Alembic (raw SQL), FastAPI, Pillow (new), pytest via `uv`.
- Flutter/Dart in `~/GitHub/open-assistant/flutter-open-assistant`.
- Kustomize in `~/GitHub/home/lab/infrastructure`.

**Spec:** `docs/superpowers/specs/2026-09-22-eve-images-design.md`, committed as `6ab4dc6`. Section references below (§x.y) point into it.

## Global Constraints

- Worktree: `/Users/nchalifo/GitHub/eve-ai/.worktrees/eve-21-images`, branch `eve-21-images`. Every `uv run` command runs from there.
- Test tiers:
  - Unit: `uv run pytest tests/<file> -v`. This is the default tier; `addopts` deselects the others.
  - Integration: `docker compose -f docker-compose.test.yml up -d`, then `uv run pytest tests/<file> -m integration -v`. Postgres is on 15432.
  - Live: `EVE_LIVE_TESTS=1 uv run pytest tests/test_live_models.py -m live -v`. This spends real quota, so run it only in Task 2.
  - Known unrelated baseline: 4 collection errors in `tests/test_acp_*` and `tests/test_computer_app.py` (`No module named 'acp'`). Ignore them.
- Every SQL statement on `eve_image` filters by `member_sub`. A foreign row and a missing row look identical to callers.
- Never put base64 or bytes in state, a tool return value, a log line, or a span attribute.
- Match the house style: module docstrings that explain *why*, and comments on non-obvious decisions. Mark deliberate shortcuts `ponytail:`.
- Commit after every task, with Conventional Commit messages scoped `images`, `ui`, `stylist` and so on, suffixed `(EVE-21)`.
- Flutter tasks run from `~/GitHub/open-assistant/flutter-open-assistant` on a new branch `eve-21-images` cut from `origin/main`: `git fetch && git switch -c eve-21-images origin/main`. Tests run with `flutter test <path>`.
- Infra tasks run from `~/GitHub/home/lab/infrastructure` on a new branch `eve-21-image-sweep` cut from `origin/main`.
- Phase order is fixed: 1, then 2 (images out), then 3 (images in). Phase 2 ships before any member photo is ever stored.

## File Structure

**Server, new (`src/eve/images/`):**

| File | Responsibility |
|---|---|
| `__init__.py` | Package docstring only |
| `process.py` | `normalise(raw) -> Normalised`: decode, EXIF-orient, strip EXIF, downscale to ≤1568 px, JPEG q85 under 1 MB. Pure and synchronous |
| `store.py` | Every SQL statement on `eve_image`: `put`, `get`, `resolve`, `set_caption`, `sweep`, `short_id`, plus the `ImageRow` dataclass |
| `app.py` | FastAPI router with `POST /images` and `GET /images/{image_id}` |
| `cli.py` | `eve-images sweep` |
| `immich.py` | `from_immich(member_sub, asset_id, *, thread_id) -> str \| None` (phase 2) |
| `hydrate.py` | `hydrate(...)`, `caption(row)`, and the reference-block helpers (phase 3) |

**Server, new tests:** `tests/test_images_process.py`, `tests/test_images_store.py` (integration), `tests/test_images_app.py`, `tests/test_images_cli.py`, `tests/test_images_migration.py`, `tests/test_images_immich.py`, `tests/test_images_hydrate.py`, `tests/test_images_checkpoint.py`.

**Server, modified:**
- `pyproject.toml`: Pillow, and the `eve-images` script.
- `alembic/versions/0014_eve_image.py`: new.
- `src/eve/settings.py`: four image settings.
- `src/eve/models.py`: `TIER_VISION`.
- `src/eve/http_app.py`: mount the router.
- `src/eve/context.py`: `principal_sub(config)`.
- `src/eve/ui/protocol.py`: the `image` component and `CATALOG_VERSIONS`.
- `src/eve/ui/stream.py`: `catalog_versions(config)`.
- `src/eve/ui/surface.py`: version stamping and `prepare_images`.
- `src/eve/ui/tools.py`: wire `prepare_images`.
- `src/eve/ui/schema.py`: `aspect` enum and the `imageId` description.
- `skills/build-a-ui/SKILL.md`: document `image`.
- `prompts/eve.md` and `prompts/stylist.md`.
- `src/eve/specialists/stylist.py`: `photo_of` tool, `accepts_images=True`.
- `src/eve/specialists/base.py`: `accepts_images`.
- `src/eve/state.py`: public `text_of`.
- `src/eve/memory/recall.py`, `src/eve/memory/extract.py`, `src/eve/title.py`, `src/eve/skills/authoring.py`: read human text through `text_of`.
- `src/eve/graph.py`: hydrate before VOICE, with the caption fallback.

**Flutter, modified:**
- Protocol and domain: `lib/data/services/agent/dynamic_surface_protocol.dart`, `lib/domain/models/dynamic_ui/dynamic_ui_capabilities.dart`.
- Renderer and catalog: `lib/ui/features/chat/dynamic_ui/dynamic_surface_catalog.dart`, `lib/ui/features/chat/dynamic_ui/dynamic_surface_renderer.dart`, `lib/data/services/dynamic_ui/dynamic_surface_cache.dart`.
- Agent service: `lib/data/services/agent/langgraph_client.dart`, `lib/data/services/agent/langgraph_agent_service.dart`, `lib/data/services/agent/agent_service.dart`.
- Repository and view model: `lib/data/repositories/agent_repository.dart`, `lib/domain/use_cases/assistant_session_use_case.dart`, `lib/ui/features/chat/view_models/conversation_view_model.dart`.
- Chat views and models: `lib/ui/features/chat/views/chat_message.dart`, `lib/domain/models/sessions/timeline_item.dart`.

**Flutter, new:**
- `lib/data/services/agent/image_loader.dart`: `ImageLoader` interface plus the LangGraph implementation.
- `lib/ui/features/chat/dynamic_ui/eve_image.dart`: the authed image widget with cache and fallback tile.

**Infra, new:** `kubernetes/apps/eve/base/image-sweep-cronjob.yaml`, added to `base/kustomization.yaml`.

---

# Phase 1: Foundation (server only, no visible change)

### Task 1: Pillow and `eve.images.process.normalise`

**Files:**
- Modify: `pyproject.toml` (`dependencies`)
- Create: `src/eve/images/__init__.py`, `src/eve/images/process.py`
- Test: `tests/test_images_process.py`

**Interfaces:**
```python
@dataclass(frozen=True)
class Normalised:
    data: bytes
    content_type: str   # always "image/jpeg"
    width: int
    height: int

MAX_SIDE = 1568
MAX_BYTES = 1_000_000

def normalise(raw: bytes) -> Normalised   # ValueError if not a decodable image
```

- [ ] **Step 1: Add the dependency**

```bash
uv add "pillow>=11.0"
```
Expected: `pyproject.toml` `dependencies` gains `pillow>=11.0`, and `uv.lock` updates.

- [ ] **Step 2: Write the failing tests**

```python
"""tests/test_images_process.py - the one place bytes are reshaped. Every
guarantee the rest of the design leans on (bounded size, no EXIF location
leaking to a model or a phone, the right way up) is asserted here."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from eve.images.process import MAX_BYTES, MAX_SIDE, normalise


def _jpeg(width: int, height: int, *, orientation: int | None = None) -> bytes:
    image = Image.new("RGB", (width, height), (200, 30, 30))
    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation  # Orientation
    exif[0x010F] = "Pixel 9"        # Make - must not survive
    buf = io.BytesIO()
    image.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def test_a_large_photo_is_downscaled_to_the_longest_side_bound():
    result = normalise(_jpeg(4000, 3000))
    assert max(result.width, result.height) == MAX_SIDE
    assert (result.width, result.height) == (1568, 1176)


def test_a_small_photo_is_not_upscaled():
    result = normalise(_jpeg(640, 480))
    assert (result.width, result.height) == (640, 480)


def test_exif_orientation_is_applied_then_stripped():
    # Orientation 6 = rotate 90 CW on display: a 4000x3000 sensor image is
    # really portrait. The stored bytes must be portrait with no EXIF at all.
    result = normalise(_jpeg(4000, 3000, orientation=6))
    assert (result.width, result.height) == (1176, 1568)
    stored = Image.open(io.BytesIO(result.data))
    assert len(stored.getexif()) == 0


def test_output_is_jpeg_under_the_byte_ceiling():
    noisy = Image.effect_noise((3000, 3000), 100).convert("RGB")
    buf = io.BytesIO()
    noisy.save(buf, "PNG")
    result = normalise(buf.getvalue())
    assert result.content_type == "image/jpeg"
    assert len(result.data) <= MAX_BYTES
    assert Image.open(io.BytesIO(result.data)).format == "JPEG"


def test_png_with_alpha_is_flattened_not_rejected():
    image = Image.new("RGBA", (100, 50), (0, 0, 0, 0))
    buf = io.BytesIO()
    image.save(buf, "PNG")
    assert normalise(buf.getvalue()).content_type == "image/jpeg"


def test_non_images_raise_value_error():
    with pytest.raises(ValueError):
        normalise(b"%PDF-1.7 not an image")
```

- [ ] **Step 3: Run it and confirm it fails**

Run: `uv run pytest tests/test_images_process.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.images'`.

- [ ] **Step 4: Implement**

`src/eve/images/__init__.py`:
```python
"""Images through Eve (EVE-21, spec docs/superpowers/specs/2026-09-22-eve-images-design.md).

An image is a row in `eve_image` addressed by an opaque UUID. Nothing else in
the system carries pixels: not state, not checkpoints, not tool arguments,
not UI frames. `hydrate` (model boundary), `app` (the phone's proxy) and
`immich` (the cache fill) are the only places bytes move.
"""
```

`src/eve/images/process.py`:
```python
"""Decode, orient, strip, downscale, re-encode - once, on write.

One size serves the model and the phone (spec 2.1): 1568 px on the longest
side is the largest size vision models use without internal downscaling, and
it is plenty for a chat tile. EXIF goes because a phone photo carries GPS in
it, and neither a model nor a later download needs to know where a member's
house is.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIDE = 1568
MAX_BYTES = 1_000_000
_QUALITIES = (85, 75, 65, 55, 45)


@dataclass(frozen=True)
class Normalised:
    data: bytes
    content_type: str
    width: int
    height: int


def normalise(raw: bytes) -> Normalised:
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("not a decodable image") from exc

    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        # Flatten alpha onto white: JPEG has no alpha, and a transparent PNG
        # rendered on black is how a logo becomes a black square.
        background = Image.new("RGB", image.size, (255, 255, 255))
        rgba = image.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)

    # ponytail: quality steps down until the ceiling holds; a photo that
    # misses it at q45 and 1568px is noise, and shrinking once more is enough.
    for quality in _QUALITIES:
        data = _encode(image, quality)
        if len(data) <= MAX_BYTES:
            break
    else:
        image.thumbnail((MAX_SIDE // 2, MAX_SIDE // 2), Image.Resampling.LANCZOS)
        data = _encode(image, _QUALITIES[-1])
    return Normalised(data, "image/jpeg", image.width, image.height)


def _encode(image: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    # No `exif=` argument: Pillow writes none unless asked, which is the strip.
    image.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()
```

- [ ] **Step 5: Run it and confirm it passes**

Run: `uv run pytest tests/test_images_process.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/eve/images tests/test_images_process.py
git commit -m "feat(images): normalise uploads with Pillow (EVE-21)"
```

---

### Task 2: The live vision probe and `TIER_VISION`

The probe comes first because it decides whether phase 3 sends pixels or captions (§3.2). Record the result; do not assume it.

**Files:**
- Modify: `tests/test_live_models.py`, `src/eve/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
```python
TIER_VISION: dict[Tier, bool]   # every Tier key present; REFLEX is True
```

- [ ] **Step 1: Add the live probes** at the end of `tests/test_live_models.py`

```python
def _red_square_b64() -> str:
    import base64
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (220, 20, 20)).save(buf, "JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def _colour_question() -> HumanMessage:
    # The standard LangChain block, exactly what `eve.images.hydrate` emits.
    return HumanMessage(
        content=[
            {"type": "text", "text": "What colour is this square? One word."},
            {"type": "image", "base64": _red_square_b64(), "mime_type": "image/jpeg"},
        ]
    )


@pytest.mark.parametrize("tier", [Tier.VOICE, Tier.MECHANICAL])
async def test_subscription_tier_sees_pixels(tier):
    """EVE-21 section 3.2: decides TIER_VISION[tier]. A failure here is a
    finding, not a bug - record False in models.py and the caption path
    carries the feature."""
    reply = await get_model(tier).ainvoke([_colour_question()])
    assert "red" in _text_of(reply).lower(), reply.content


async def test_fallback_model_sees_pixels():
    settings = get_settings()
    model = ChatOpenAI(
        model="anthropic/claude-sonnet-5",
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key or "unset",
        use_responses_api=False,
    )
    reply = await model.ainvoke([_colour_question()])
    assert "red" in _text_of(reply).lower(), reply.content
```

- [ ] **Step 2: Run the probe (live, spends quota)**

Run: `EVE_LIVE_TESTS=1 uv run pytest tests/test_live_models.py -m live -v -k "pixels"`

Record each tier's pass or fail. If a tier errors (for example the proxy rejects `input_image`), paste the error into the `TIER_VISION` comment in Step 4. If `EVE_LIVE_TESTS` credentials are unavailable in this environment, **stop and ask the user to run the probe**. Do not guess.

- [ ] **Step 3: Write the failing unit test** in `tests/test_models.py`

```python
def test_tier_vision_names_every_tier_and_reflex_sees():
    from eve.models import TIER_VISION, Tier

    assert set(TIER_VISION) == set(Tier)
    # wardrobe/vision.py has sent REFLEX images since EVE-20; the caption
    # path depends on it.
    assert TIER_VISION[Tier.REFLEX] is True
```

Run: `uv run pytest tests/test_models.py -v -k tier_vision`
Expected: FAIL with `ImportError: cannot import name 'TIER_VISION'`.

- [ ] **Step 4: Implement.** Add this to `src/eve/models.py` directly under `TIER_MODELS`, filling in the booleans from Step 2:

```python
# Which tiers are handed native image blocks (EVE-21, spec 3.2). Decided by
# tests/test_live_models.py::test_subscription_tier_sees_pixels on <DATE>,
# not assumed: <paste probe outcome per tier, and any proxy error>.
# False means `eve.images.hydrate` sends a cached REFLEX caption instead, so
# the feature still works end to end with less visual detail. Keyed to the
# primary model, like `use_responses_api`: a fallback hop that refuses
# pixels is handled at call time (graph.py's caption fallback), not here.
TIER_VISION: dict[Tier, bool] = {
    Tier.VOICE: <probe result>,
    Tier.DEEP: <same model as VOICE's family - use the terra/sol result; False if unprobed>,
    Tier.MECHANICAL: <probe result>,
    Tier.CODE: False,   # never handed images
    Tier.REFLEX: True,  # wardrobe/vision.py
}
```

The `<...>` markers are values the probe produces. They must be replaced with literal `True` or `False` before committing.

- [ ] **Step 5: Run and confirm the test passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_live_models.py tests/test_models.py src/eve/models.py
git commit -m "feat(models): probe tier vision and record TIER_VISION (EVE-21)"
```

---

### Task 3: Image settings

**Files:**
- Modify: `src/eve/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
```python
image_retention_days: int = 30
image_cache_days: int = 1
image_hydrate_turns: int = 2
image_max_upload_bytes: int = 15 * 1024 * 1024
```

- [ ] **Step 1: Write the failing test** in `tests/test_settings.py`

```python
def test_image_settings_default_to_the_spec(monkeypatch):
    from eve.settings import Settings

    s = Settings()
    assert s.image_retention_days == 30
    assert s.image_cache_days == 1
    assert s.image_hydrate_turns == 2
    assert s.image_max_upload_bytes == 15 * 1024 * 1024
```

Run: `uv run pytest tests/test_settings.py -v -k image_settings`
Expected: FAIL with `AttributeError`.

- [ ] **Step 2: Implement.** Add these fields to `Settings` next to the routine fields:

```python
    # EVE-21 images. A member photo lives 30 days by explicit decision, not
    # forever by default; an Immich copy is a cache of something Immich
    # already keeps, so a day is enough (spec 2.4).
    image_retention_days: int = 30
    image_cache_days: int = 1
    # How many recent human turns get their images re-sent as pixels. Older
    # ones become "[image <id>: <alt>]" text, so a long thread does not
    # re-upload every photo on every turn (spec 3.2).
    image_hydrate_turns: int = 2
    # Refused with 413 before decoding. The phone's picker output is well
    # under this; the ceiling exists so a decompression bomb costs nothing.
    image_max_upload_bytes: int = 15 * 1024 * 1024
```

- [ ] **Step 3: Run and confirm it passes**

Run: `uv run pytest tests/test_settings.py -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/eve/settings.py tests/test_settings.py
git commit -m "feat(images): retention and hydration settings (EVE-21)"
```

---

### Task 4: Migration `0014_eve_image`

**Files:**
- Create: `alembic/versions/0014_eve_image.py`
- Test: `tests/test_images_migration.py`; `tests/test_alembic_graph.py` (already exists, re-run)

- [ ] **Step 1: Write the failing test**

```python
"""tests/test_images_migration.py"""
import importlib.util
from pathlib import Path


def _migration():
    path = Path("alembic/versions/0014_eve_image.py")
    spec = importlib.util.spec_from_file_location("m0014", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0014_follows_0013_and_creates_the_table(monkeypatch):
    migration = _migration()
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert migration.down_revision == "0013_eve_coding_session_review"
    sql = "\n".join(statements)
    assert "CREATE TABLE IF NOT EXISTS eve_image" in sql
    assert "bytes bytea NOT NULL" in sql
    assert "UNIQUE (member_sub, source_ref)" in sql
    assert "eve_image_expires_at_idx" in sql
```

Run: `uv run pytest tests/test_images_migration.py -v`
Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 2: Implement `alembic/versions/0014_eve_image.py`**

```python
"""eve_image: image bytes addressed by opaque id (EVE-21, spec 2.1).

bytea in Eve's own Postgres rather than SeaweedFS or a PVC: nothing new to
deploy, CNPG backups already cover it, and retention is a DELETE. Revisit
if the table passes a few GB.

`UNIQUE (member_sub, source_ref)` is the Immich dedupe. Postgres treats NULLs
as distinct, so uploads (source_ref NULL) never collide with each other.
"""

from alembic import op

revision = "0014_eve_image"
down_revision = "0013_eve_coding_session_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS eve_image (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            member_sub text NOT NULL,
            thread_id text,
            origin text NOT NULL CHECK (origin IN ('upload', 'immich')),
            source_ref text,
            content_type text NOT NULL,
            bytes bytea NOT NULL,
            width integer NOT NULL,
            height integer NOT NULL,
            caption text,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            UNIQUE (member_sub, source_ref)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS eve_image_expires_at_idx ON eve_image (expires_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS eve_image_member_thread_idx"
        " ON eve_image (member_sub, thread_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS eve_image")
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_images_migration.py tests/test_alembic_graph.py -v`
Expected: 2 passed, and the migration graph still has exactly one head.

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/0014_eve_image.py tests/test_images_migration.py
git commit -m "feat(images): eve_image table (EVE-21)"
```

---

### Task 5: `eve.images.store`

**Files:**
- Create: `src/eve/images/store.py`
- Test: `tests/test_images_store.py` (integration)

**Interfaces:**
```python
@dataclass(frozen=True)
class ImageRow:
    id: str; member_sub: str; thread_id: str | None; origin: str
    source_ref: str | None; content_type: str; bytes: bytes
    width: int; height: int; caption: str | None
    created_at: datetime; expires_at: datetime

def short_id(image_id: str) -> str                      # first 8 hex chars
async def put(member_sub, image: Normalised, *, origin, thread_id=None,
              source_ref=None, now=None) -> ImageRow     # upsert on (member_sub, source_ref)
async def get(image_id, member_sub, *, include_expired=False, now=None) -> ImageRow | None
async def resolve(ref, member_sub, thread_id, *, now=None) -> ImageRow | None
async def set_caption(image_id, member_sub, caption) -> None
async def sweep(now=None) -> int
```

Design notes:
- `put` takes an already-`Normalised` image rather than raw bytes. Decoding is CPU work, and it belongs outside the connection. This is the one deliberate deviation from the spec's `put(member_sub, raw, ...)` signature.
- `resolve` returns the row, not just the id. Every caller (surface provenance, `aspect`, specialist ids) also needs the dimensions or the member check, so this saves them a second query.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run and confirm failure**

Run: `docker compose -f docker-compose.test.yml up -d && uv run pytest tests/test_images_store.py -m integration -v`
Expected: FAIL with `ImportError: cannot import name 'store'`.

- [ ] **Step 3: Implement `src/eve/images/store.py`**

```python
"""Every SQL statement on `eve_image`. Same discipline as `eve/routines/store.py`:
one module owns the table, and every statement filters by member_sub, so a
foreign row and a missing row are indistinguishable to every caller (and to
the phone, which gets 404 for both)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from psycopg.rows import dict_row

from eve.images.process import Normalised
from eve.memory.db import get_pool
from eve.settings import get_settings

_FULL = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SHORT = re.compile(r"^[0-9a-f]{8}$")


@dataclass(frozen=True)
class ImageRow:
    id: str
    member_sub: str
    thread_id: str | None
    origin: str
    source_ref: str | None
    content_type: str
    bytes: bytes
    width: int
    height: int
    caption: str | None
    created_at: datetime
    expires_at: datetime


def short_id(image_id: str) -> str:
    """What a model cites: `[image 3f2a9c01]`. Eight hex characters is 4
    billion values per member-thread - a collision is refused by `resolve`,
    never guessed."""
    return image_id[:8]


def is_full_id(value: object) -> bool:
    return isinstance(value, str) and bool(_FULL.match(value))


def _row(record: dict) -> ImageRow:
    return ImageRow(**{**record, "id": str(record["id"]), "bytes": bytes(record["bytes"])})


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


async def put(
    member_sub: str,
    image: Normalised,
    *,
    origin: str,
    thread_id: str | None = None,
    source_ref: str | None = None,
    now: datetime | None = None,
) -> ImageRow:
    settings = get_settings()
    days = settings.image_cache_days if origin == "immich" else settings.image_retention_days
    created = _now(now)
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # ON CONFLICT refreshes an Immich copy in place, so its id - which
            # may already sit in a persisted surface - stays valid. Uploads
            # never conflict: source_ref is NULL and NULLs are distinct.
            await cur.execute(
                "INSERT INTO eve_image (member_sub, thread_id, origin, source_ref,"
                " content_type, bytes, width, height, created_at, expires_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (member_sub, source_ref) DO UPDATE SET"
                " thread_id = EXCLUDED.thread_id, content_type = EXCLUDED.content_type,"
                " bytes = EXCLUDED.bytes, width = EXCLUDED.width,"
                " height = EXCLUDED.height, caption = NULL,"
                " created_at = EXCLUDED.created_at, expires_at = EXCLUDED.expires_at"
                " RETURNING *",
                (
                    member_sub, thread_id, origin, source_ref, image.content_type,
                    image.data, image.width, image.height, created,
                    created + timedelta(days=days),
                ),
            )
            return _row(await cur.fetchone())


async def get(
    image_id: str,
    member_sub: str,
    *,
    include_expired: bool = False,
    now: datetime | None = None,
) -> ImageRow | None:
    """None for missing, foreign, malformed, or (by default) expired.
    `include_expired` exists for one caller - the GET route - which must tell
    410 from 404."""
    if not is_full_id(image_id):
        return None
    sql = "SELECT * FROM eve_image WHERE id = %s AND member_sub = %s"
    params: list = [image_id, member_sub]
    if not include_expired:
        sql += " AND expires_at > %s"
        params.append(_now(now))
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            record = await cur.fetchone()
            return _row(record) if record else None


async def resolve(
    ref: str, member_sub: str, thread_id: str | None, *, now: datetime | None = None
) -> ImageRow | None:
    """A full or short id, as a model or specialist wrote it, back to a live
    row this member owns in this thread. The single provenance check for every
    consumer (spec 3.2): a model cannot place an id it was never shown."""
    ref = (ref or "").strip().lower()
    if is_full_id(ref):
        clause, value = "id = %s", ref
    elif _SHORT.match(ref):
        clause, value = "id::text LIKE %s", ref + "%"
    else:
        return None
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT * FROM eve_image WHERE {clause} AND member_sub = %s"
                " AND thread_id IS NOT DISTINCT FROM %s AND expires_at > %s LIMIT 2",
                (value, member_sub, thread_id, _now(now)),
            )
            records = await cur.fetchall()
    return _row(records[0]) if len(records) == 1 else None


async def set_caption(image_id: str, member_sub: str, caption: str) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_image SET caption = %s WHERE id = %s AND member_sub = %s",
            (caption, image_id, member_sub),
        )


async def sweep(now: datetime | None = None) -> int:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM eve_image WHERE expires_at <= %s", (_now(now),)
        )
        return cur.rowcount
```

- [ ] **Step 4: Run and confirm it passes**

Run: `uv run pytest tests/test_images_store.py -m integration -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/eve/images/store.py tests/test_images_store.py
git commit -m "feat(images): member-scoped image store (EVE-21)"
```

---

### Task 6: `/images` routes

**Files:**
- Create: `src/eve/images/app.py`
- Modify: `src/eve/http_app.py`
- Test: `tests/test_images_app.py`

**Interfaces:**
- `POST /images`, multipart with field `file` (required) and `thread_id` (optional).
  - 200 returns `{"image_id", "width", "height"}`.
  - 413 over `image_max_upload_bytes`. 415 when the upload does not decode.
- `GET /images/{image_id}` returns bytes with `Content-Type`, `Cache-Control: private, max-age=3600`.
  - 404 for missing, foreign, or malformed ids. 410 for expired ones.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run and confirm failure**

Run: `uv run pytest tests/test_images_app.py -v`
Expected: FAIL with `ImportError: cannot import name 'app' from 'eve.images'`.

- [ ] **Step 3: Implement `src/eve/images/app.py`**

```python
"""`POST /images` and `GET /images/{id}` - Eve is the image proxy by
construction (spec 2.3, 4.1). The phone only ever fetches its own Eve base
URL with the bearer it already sends; it never learns an Immich URL and never
fetches a model-chosen one.

Owner-only reads. Household sharing is a one-line change to the `get` call
below if the family ever wants it (spec 8).
"""

from __future__ import annotations

from datetime import UTC, datetime

from aegra_api.core.auth_deps import require_auth
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from eve.images import store
from eve.images.process import normalise
from eve.settings import get_settings

router = APIRouter(dependencies=[Depends(require_auth)])


def current_member(request: Request) -> dict:
    """Same contract as `eve.routines.app.current_member`; overridden in tests."""
    user = request.scope.get("user")
    if user is None or not getattr(user, "identity", None):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {"sub": user.identity, "permissions": list(getattr(user, "permissions", []) or [])}


@router.post("/images")
async def upload(
    file: UploadFile,
    thread_id: str | None = Form(default=None),
    member: dict = Depends(current_member),
) -> dict:
    limit = get_settings().image_max_upload_bytes
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(status_code=413, detail="image too large")
    try:
        # Decoding a 12 MP photo is ~100 ms of CPU; off the event loop so
        # one upload does not stall every streaming turn on this pod.
        image = await run_in_threadpool(normalise, raw)
    except ValueError:
        raise HTTPException(status_code=415, detail="not an image") from None
    row = await store.put(member["sub"], image, origin="upload", thread_id=thread_id)
    return {"image_id": row.id, "width": row.width, "height": row.height}


@router.get("/images/{image_id}")
async def download(image_id: str, member: dict = Depends(current_member)) -> Response:
    row = await store.get(image_id, member["sub"], include_expired=True)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if row.expires_at <= datetime.now(UTC):
        # Distinct from 404 so the phone can say "no longer available" rather
        # than "something is broken" (spec 4.3).
        raise HTTPException(status_code=410, detail="expired")
    return Response(
        content=row.bytes,
        media_type=row.content_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )
```

In `src/eve/http_app.py`, add the import next to the other routers and mount the router:

```python
from eve.images.app import router as images_router
...
app.include_router(images_router)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_images_app.py tests/test_http_app.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/eve/images/app.py src/eve/http_app.py tests/test_images_app.py
git commit -m "feat(images): owner-only upload and download routes (EVE-21)"
```

---

### Task 7: `eve-images sweep` CLI

**Files:**
- Create: `src/eve/images/cli.py`
- Modify: `pyproject.toml` (`[project.scripts]`)
- Test: `tests/test_images_cli.py`

- [ ] **Step 1: Write the failing test**

```python
"""tests/test_images_cli.py"""
import sys


def test_sweep_deletes_and_reports(monkeypatch, capsys):
    from eve.images import cli

    closed = []

    async def fake_sweep(now=None):
        return 3

    async def fake_close():
        closed.append(True)

    monkeypatch.setattr(cli.store, "sweep", fake_sweep)
    monkeypatch.setattr(cli, "close_pool", fake_close)
    monkeypatch.setattr(sys, "argv", ["eve-images", "sweep"])

    cli.main()

    assert "deleted 3 expired image(s)" in capsys.readouterr().out
    assert closed == [True]
```

Run: `uv run pytest tests/test_images_cli.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 2: Implement `src/eve/images/cli.py`**

```python
"""eve-images: maintenance for the image store.

`sweep` is run nightly by a Kubernetes CronJob (infrastructure repo,
kubernetes/apps/eve/base/image-sweep-cronjob.yaml). Reads already refuse
expired rows, so a missed night costs disk, never privacy.
"""

from __future__ import annotations

import argparse
import asyncio

from eve.images import store
from eve.memory.db import close_pool


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sweep", help="delete expired images")
    parser.parse_args()

    async def _run() -> None:
        try:
            deleted = await store.sweep()
            print(f"deleted {deleted} expired image(s)")
        finally:
            await close_pool()

    asyncio.run(_run())
```

Add this to `pyproject.toml` `[project.scripts]`, alphabetically next to `eve-migrate`:

```toml
eve-images = "eve.images.cli:main"
```

Then run `uv sync`.

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_images_cli.py -v && uv run eve-images --help`
Expected: 1 passed, and the help text lists `sweep`.

- [ ] **Step 4: Commit**

```bash
git add src/eve/images/cli.py pyproject.toml uv.lock tests/test_images_cli.py
git commit -m "feat(images): eve-images sweep CLI (EVE-21)"
```

---

### Task 8: Nightly sweep CronJob (infra repo)

**Files (in `~/GitHub/home/lab/infrastructure`):**
- Create: `kubernetes/apps/eve/base/image-sweep-cronjob.yaml`
- Modify: `kubernetes/apps/eve/base/kustomization.yaml`

The overlay's `images:` pin rewrites `ghcr.io/noahchalifour/eve-ai` in every resource, CronJobs included, so the job always runs the deployed tag.

- [ ] **Step 1: Branch**

```bash
cd ~/GitHub/home/lab/infrastructure && git fetch && git switch -c eve-21-image-sweep origin/main
```

- [ ] **Step 2: Create the CronJob**

```yaml
# Nightly deletion of expired eve_image rows (eve-ai EVE-21, spec 2.4).
# Uploads live 30 days and Immich cache copies 1 day, by setting. Eve refuses
# expired rows at read time too, so a missed run costs disk, never privacy.
apiVersion: batch/v1
kind: CronJob
metadata:
  name: eve-image-sweep
  namespace: eve
spec:
  schedule: "17 4 * * *"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 1
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 2
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: sweep
              image: ghcr.io/noahchalifour/eve-ai
              command: ["eve-images", "sweep"]
              env:
                - name: DATABASE_URL
                  valueFrom:
                    secretKeyRef:
                      name: eve-db-app
                      key: uri
              securityContext:
                runAsNonRoot: true
                runAsUser: 10001
                allowPrivilegeEscalation: false
                capabilities:
                  drop: ["ALL"]
```

Add `- image-sweep-cronjob.yaml` to `resources:` in `base/kustomization.yaml`, after `deployment.yaml`.

- [ ] **Step 3: Verify it renders with the pinned tag**

Run: `kubectl kustomize kubernetes/apps/eve/overlays/homelab | grep -A3 "name: sweep"`
Expected: `image: ghcr.io/noahchalifour/eve-ai:v0.10.1`, or whatever the current pin is.

Do **not** merge this before an eve-ai release that contains `eve-images`. Hold the PR and ship it with the release-eve skill's image-pin bump.

- [ ] **Step 4: Commit**

```bash
git add kubernetes/apps/eve/base/image-sweep-cronjob.yaml kubernetes/apps/eve/base/kustomization.yaml
git commit -m "feat(eve): nightly eve_image sweep CronJob (EVE-21)"
```

**Phase 1 checkpoint:** run `uv run pytest -q` (unit tier) and `uv run pytest tests/test_images_store.py -m integration -q`. Both must be green apart from the known `acp` baseline.

---
# Phase 2 — Images out (server, then the coordinated Flutter release)

### Task 9: `principal_sub(config)` — one reader for "who is this"

Surface tools run in Eve's own loop, where `config.configurable.member` is not set (only `build_specialist`'s inner config sets it); specialist tools run inside it, where it is. Image code runs in both, so it needs one tolerant reader.

**Files:**
- Modify: `src/eve/context.py`
- Test: `tests/test_context.py` (append)

**Interfaces:**
```python
def principal_sub(config: RunnableConfig | None) -> str | None
```

- [ ] **Step 1: Failing test**

```python
def test_principal_sub_reads_every_shape():
    from types import SimpleNamespace

    from eve.context import principal_sub

    assert principal_sub({"configurable": {"member": {"sub": "sub-a"}}}) == "sub-a"
    assert principal_sub({"configurable": {"langgraph_auth_user": {"identity": "sub-b"}}}) == "sub-b"
    assert principal_sub(
        {"configurable": {"langgraph_auth_user": SimpleNamespace(identity="sub-c")}}
    ) == "sub-c"
    assert principal_sub({}) is None
    assert principal_sub(None) is None
```

Run: `uv run pytest tests/test_context.py -v -k principal_sub` → FAIL (`ImportError`).

- [ ] **Step 2: Implement** in `src/eve/context.py`, and make `load_context` use it:

```python
def principal_sub(config: RunnableConfig | None) -> str | None:
    """The member this run acts for. `configurable.member` inside a
    specialist's loop (build_specialist sets it); otherwise Aegra's principal,
    which is a pydantic `User` in production and a dict in tests - the same
    tolerance `load_context` has always needed."""
    configurable = (config or {}).get("configurable") or {}
    member = configurable.get("member")
    if isinstance(member, Mapping) and member.get("sub"):
        return member["sub"]
    principal = configurable.get("langgraph_auth_user")
    if principal is None:
        return None
    if isinstance(principal, Mapping):
        return principal.get("identity")
    return getattr(principal, "identity", None)
```

In `load_context`, replace the inline `principal`/`identity` lines with `identity = principal_sub(config)` (keep the existing comment above it).

- [ ] **Step 3: Run** `uv run pytest tests/test_context.py tests/test_graph.py -v` → all pass.

- [ ] **Step 4: Commit**

```bash
git add src/eve/context.py tests/test_context.py
git commit -m "refactor(context): principal_sub reads the run's member in every shape (EVE-21)"
```

---

### Task 10: `image` in the protocol, with version-scoped catalogs

**Files:**
- Modify: `src/eve/ui/protocol.py`, `tests/test_ui_protocol.py`

**Interfaces:**
```python
CATALOG_IDS_V1: frozenset[str]              # today's 15 ids
CATALOG_VERSIONS: dict[str, frozenset[str]] = {"1": CATALOG_IDS_V1, "2": CATALOG_IDS_V1 | {"image"}}
CATALOG_IDS: frozenset[str]                 # union of every version - "every type this server can validate"
CATALOG_VERSION = "1"                       # the baseline; unchanged meaning for stream.supports
IMAGE_VERSION = "2"
IMAGE_ASPECTS = frozenset({"square", "portrait", "landscape"})
```

`CATALOG_IDS` widening to include `image` is deliberate: `schema.py`, the skill-doc parity test and `_static_tools`' intersection all mean "every type this server knows". What a given surface may contain is now `CATALOG_VERSIONS[surface.catalogVersion]`.

- [ ] **Step 1: Failing tests** — append to `tests/test_ui_protocol.py`, and change the existing `test_a_catalog_version_other_than_1_is_rejected` to use `catalogVersion="3"` (rename it `test_an_unknown_catalog_version_is_rejected`):

```python
IMAGE_ID = "3f2a9c01-0000-4000-8000-000000000001"


def _image(**properties) -> dict:
    return {
        "id": "i1",
        "type": "image",
        "properties": {"imageId": IMAGE_ID, "alt": "navy blazer", **properties},
        "children": [],
    }


def test_an_image_surface_is_valid_at_version_2():
    assert protocol.validate_operation(
        _surface(catalogVersion="2", components=[_image(aspect="portrait")])
    ) is None


def test_an_image_is_illegal_in_a_version_1_surface():
    assert protocol.validate_operation(
        _surface(components=[_image()])
    ) == "component-type"


def test_a_version_2_surface_without_an_image_is_still_valid():
    assert protocol.validate_operation(_surface(catalogVersion="2")) is None


def test_image_requires_image_id_and_alt():
    for missing in ("imageId", "alt"):
        component = _image()
        del component["properties"][missing]
        assert protocol.validate_operation(
            _surface(catalogVersion="2", components=[component])
        ) == "component-schema", missing


def test_image_id_must_be_a_full_uuid_never_a_url():
    for bad in ("3f2a9c01", "https://evil.example/x.jpg", "", 7):
        assert protocol.validate_operation(
            _surface(catalogVersion="2", components=[_image(imageId=bad)])
        ) == "component-schema", bad


def test_aspect_is_a_closed_set():
    assert protocol.validate_operation(
        _surface(catalogVersion="2", components=[_image(aspect="wide")])
    ) == "component-schema"


def test_alt_is_a_bounded_non_empty_string():
    for bad in ("", "x" * (protocol.MAX_STRING + 1), 3):
        assert protocol.validate_operation(
            _surface(catalogVersion="2", components=[_image(alt=bad)])
        ) is not None, bad


def test_image_is_chat_only_never_in_a_widget_snapshot():
    assert protocol.validate_operation(
        _surface(catalogVersion="2", components=[_image()]), widget=True
    ) == "component-type"


def test_catalog_versions_nest():
    assert protocol.CATALOG_VERSIONS["1"] < protocol.CATALOG_VERSIONS["2"]
    assert protocol.CATALOG_VERSIONS["2"] - protocol.CATALOG_VERSIONS["1"] == {"image"}
    assert protocol.CATALOG_IDS == protocol.CATALOG_VERSIONS["2"]
```

Run: `uv run pytest tests/test_ui_protocol.py -v` → the new tests FAIL.

- [ ] **Step 2: Implement** in `src/eve/ui/protocol.py`:

1. Rename today's `CATALOG_IDS = frozenset({...})` to `CATALOG_IDS_V1`, then below it:

```python
# EVE-21: version 2 is version 1 plus `image`. A surface is stamped with the
# LOWEST version that holds its components (eve.ui.surface), so every
# surface without a photo stays readable by a phone that predates this.
IMAGE_VERSION = "2"
CATALOG_VERSIONS: dict[str, frozenset[str]] = {
    "1": CATALOG_IDS_V1,
    IMAGE_VERSION: CATALOG_IDS_V1 | {"image"},
}
# Every type this server can validate, whatever the version. The schema, the
# skill document and graph._static_tools' intersection all mean this.
CATALOG_IDS = frozenset().union(*CATALOG_VERSIONS.values())
IMAGE_ASPECTS = frozenset({"square", "portrait", "landscape"})
_IMAGE_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
```

2. `_ALLOWED_PROPERTIES["image"] = frozenset({"imageId", "alt", "aspect"})`, with a comment: no URL property, ever — the client fetches only `GET /images/{imageId}` on Eve's own base URL (spec 4.1).

3. `_validate_create`: replace the `!= CATALOG_VERSION` check and the catalog lookup with:

```python
    allowed = CATALOG_VERSIONS.get(surface["catalogVersion"])
    if allowed is None:
        return "catalog-version"
    if surface["catalogId"] not in CATALOG_IDS_V1:
        return "catalog"
    error = _validate_components(
        surface.get("components", []), widget=widget, allowed=allowed
    )
```

4. `_validate_components(components, *, widget=False, allowed=CATALOG_IDS)`: check `component["type"] not in allowed` instead of `CATALOG_IDS`, and additionally `if widget and component["type"] == "image": return "component-type"` (comment: widget snapshots refresh on a schedule and need their own image-lifetime design; spec 4.1). `_validate_patch` keeps the default `allowed=CATALOG_IDS` — a patch does not carry a version, and Eve never patches an image in.

5. `_validate_property`: add before the final `return "component-schema"`:

```python
    if key == "imageId":
        return None if isinstance(value, str) and _IMAGE_ID.match(value) else "component-schema"
    if key == "alt":
        if not isinstance(value, str) or not value.strip():
            return "component-schema"
        return validate_json_value(value)
    if key == "aspect":
        return None if value in IMAGE_ASPECTS else "component-schema"
```

(`validate_json_value` returns `string-limit` for strings over `MAX_STRING` — verified — so the `alt` bound needs nothing extra.)

6. `_validate_properties`: after the `button` rule,

```python
    if component_type == "image" and not {"imageId", "alt"} <= properties.keys():
        return "component-schema"
```

- [ ] **Step 3: Run the whole UI suite** — `CATALOG_IDS` widened, so every consumer re-runs:

Run: `uv run pytest tests/test_ui_protocol.py tests/test_ui_schema.py tests/test_ui_tools.py tests/test_ui_stream.py tests/test_ui_surface.py tests/test_ui_persist.py tests/test_graph.py tests/test_widgets_resolve.py tests/test_skills_build_a_ui.py -v`

Expected: everything passes **except** `tests/test_skills_build_a_ui.py::test_the_skill_documents_every_component_type` and `tests/test_ui_schema.py` property-constraint checks for `image` if any — those are fixed in Task 11. Nothing else may fail; if `test_ui_tools.py` fails because `show_surface`'s unknown-type message now lists `image`, that is correct — update the expectation.

- [ ] **Step 4: Commit**

```bash
git add src/eve/ui/protocol.py tests/test_ui_protocol.py
git commit -m "feat(ui): image component and version-scoped catalogs (EVE-21)"
```

---

### Task 11: Schema and skill document learn `image`

**Files:**
- Modify: `src/eve/ui/schema.py`, `skills/build-a-ui/SKILL.md`, `prompts/eve.md`
- Test: `tests/test_ui_schema.py`, `tests/test_skills_build_a_ui.py` (existing, must go green)

- [ ] **Step 1: Failing test** — append to `tests/test_ui_schema.py`:

```python
def test_the_image_branch_says_what_the_validator_enforces(full):
    image = _branches(full)["image"]
    assert image["required"] == ["alt", "imageId"]
    assert set(image["properties"]["aspect"]["enum"]) == set(protocol.IMAGE_ASPECTS)
    # The short form is accepted from the model and rewritten to the full id
    # by eve.ui.surface.prepare_images before validation.
    assert "[image" in image["properties"]["imageId"]["description"]
```

Run: `uv run pytest tests/test_ui_schema.py -v` → FAIL.

- [ ] **Step 2: Implement** in `src/eve/ui/schema.py`:

`_property`:
```python
    if name == "imageId":
        return {
            "type": "string",
            "description": (
                "The id of an image you were shown, as it appeared: "
                "`[image 3f2a9c01]` means imageId `3f2a9c01`. Never invent one."
            ),
        }
    if name == "aspect":
        return {"type": "string", "enum": sorted(protocol.IMAGE_ASPECTS)}
    if name == "alt":
        return {"type": "string", "description": "What the image shows, in a few words."}
```

`_properties_for`: add `"required": ["alt", "imageId"]` when `kind == "image"` (sorted, matching the test). Keep the budget test green (`< 12_000` compact bytes; measured 8,644 before this task).

- [ ] **Step 3: Skill document** — in `skills/build-a-ui/SKILL.md` `## The catalog`, add after `segmentedSelection`:

```markdown
- `image`: alt, aspect, imageId
```

and a paragraph after the constraints paragraph:

```markdown
`image` shows a photo you were given an id for - one a specialist cited as
`[image 3f2a9c01]`, or one the member sent. Use that id as `imageId`; never
make one up, and never put a web address anywhere. `alt` says what it shows.
`aspect` is optional (`square`, `portrait`, `landscape`) and filled in for you
when omitted. Images only work in chat, not in saved widgets.
```

- [ ] **Step 4: Eve's prompt** — in `prompts/eve.md`, in the surfaces bullet list, add:

```markdown
- When a specialist's answer cites a photo as `[image 3f2a9c01]`, show it with
  an `image` component rather than describing it. Only ever use ids you were
  given.
```

- [ ] **Step 5: Run** `uv run pytest tests/test_ui_schema.py tests/test_skills_build_a_ui.py tests/test_ui_tools.py -v` → all pass.

- [ ] **Step 6: Commit**

```bash
git add src/eve/ui/schema.py skills/build-a-ui/SKILL.md prompts/eve.md tests/test_ui_schema.py
git commit -m "feat(ui): describe the image component to the model (EVE-21)"
```

---

### Task 12: `prepare_images` — downgrade, provenance, aspect, stamping

**Files:**
- Modify: `src/eve/ui/stream.py`, `src/eve/ui/surface.py`, `src/eve/ui/tools.py`
- Test: `tests/test_ui_surface.py`, `tests/test_ui_stream.py`, `tests/test_ui_tools.py`

**Interfaces:**
```python
# stream.py
def catalog_versions(config) -> frozenset[str]     # configurable.catalog_versions ∩ known; always ⊇ {"1"}

# surface.py
async def prepare_images(components: list, config) -> tuple[list, bool]
    # -> (new tree, contains_image). Never mutates `components`.
def build_create(surface_id, components, *, catalog_version=protocol.CATALOG_VERSION) -> dict
def aspect_of(width: int, height: int) -> str
```

Rules (spec 4.1, 4.2), applied to every `image` node at any depth, in this order:
1. Run did not advertise `"2"` → replace with `{"id", "type": "text", "properties": {"text": alt}}`.
2. `store.resolve(imageId, principal_sub(config), configurable.thread_id)` is `None` → same text replacement, plus `logger.warning("image %s did not resolve for this member and thread; shown as text", imageId)`.
3. Otherwise rewrite `imageId` to the row's full id and set `aspect` from `aspect_of(row.width, row.height)` if the model omitted it.

A malformed image node (non-dict properties, non-string `alt`) is left untouched so `validate_operation` rejects it with a diagnostic the model can act on.

- [ ] **Step 1: Failing tests**

`tests/test_ui_stream.py`:
```python
def test_catalog_versions_defaults_to_the_baseline():
    assert stream.catalog_versions({}) == frozenset({"1"})
    assert stream.catalog_versions({"configurable": {"catalog_versions": ["1", "2"]}}) == {"1", "2"}
    # Unknown versions are ignored rather than trusted; junk is ignored.
    assert stream.catalog_versions({"configurable": {"catalog_versions": ["2", "9"]}}) == {"1", "2"}
    assert stream.catalog_versions({"configurable": {"catalog_versions": "2"}}) == {"1"}
```

`tests/test_ui_surface.py`:
```python
from datetime import UTC, datetime, timedelta

import pytest

from eve.images.store import ImageRow

FULL = "3f2a9c01-0000-4000-8000-000000000001"
V2 = {"configurable": {"catalog_versions": ["1", "2"], "thread_id": "t1",
                       "member": {"sub": "sub-noah"}}}
V1 = {"configurable": {"thread_id": "t1", "member": {"sub": "sub-noah"}}}


def _row(width=600, height=900):
    now = datetime.now(UTC)
    return ImageRow(FULL, "sub-noah", "t1", "immich", "a-1", "image/jpeg", b"",
                    width, height, None, now, now + timedelta(days=1))


def _tree(image_id="3f2a9c01", **extra):
    return [{"id": "c", "type": "card", "properties": {"title": "Today"}, "children": [
        {"id": "i", "type": "image",
         "properties": {"imageId": image_id, "alt": "navy blazer", **extra}}]}]


@pytest.fixture
def resolved(monkeypatch):
    calls = []

    async def fake_resolve(ref, member_sub, thread_id, *, now=None):
        calls.append((ref, member_sub, thread_id))
        return _row() if ref in ("3f2a9c01", FULL) else None

    monkeypatch.setattr(surface.store, "resolve", fake_resolve)
    return calls


async def test_a_v2_run_gets_the_full_id_and_a_filled_aspect(resolved):
    tree = _tree()
    prepared, has_image = await surface.prepare_images(tree, V2)
    image = prepared[0]["children"][0]
    assert has_image is True
    assert image["properties"] == {"imageId": FULL, "alt": "navy blazer", "aspect": "portrait"}
    assert resolved == [("3f2a9c01", "sub-noah", "t1")]
    assert tree[0]["children"][0]["properties"]["imageId"] == "3f2a9c01"  # not mutated


async def test_a_model_supplied_aspect_is_kept(resolved):
    prepared, _ = await surface.prepare_images(_tree(aspect="square"), V2)
    assert prepared[0]["children"][0]["properties"]["aspect"] == "square"


async def test_a_v1_run_downgrades_to_text_without_touching_the_store(resolved):
    prepared, has_image = await surface.prepare_images(_tree(), V1)
    assert has_image is False
    assert prepared[0]["children"][0] == {
        "id": "i", "type": "text", "properties": {"text": "navy blazer"}}
    assert resolved == []


async def test_an_unresolvable_id_becomes_text_and_is_logged(resolved, caplog):
    prepared, has_image = await surface.prepare_images(_tree(image_id="deadbeef"), V2)
    assert has_image is False
    assert prepared[0]["children"][0]["type"] == "text"
    assert "did not resolve" in caplog.text


async def test_a_tree_without_images_is_returned_as_is(resolved):
    prepared, has_image = await surface.prepare_images(COMPONENTS, V2)
    assert (prepared, has_image) == (COMPONENTS, False)


def test_aspect_of_thresholds():
    assert surface.aspect_of(1000, 1000) == "square"
    assert surface.aspect_of(1100, 1000) == "square"
    assert surface.aspect_of(1568, 1176) == "landscape"
    assert surface.aspect_of(1176, 1568) == "portrait"


def test_build_create_stamps_the_version_it_is_given():
    op = surface.build_create("sf-1", COMPONENTS, catalog_version="2")
    assert op["surface"]["catalogVersion"] == "2"
```

`tests/test_ui_tools.py` — end-to-end through `show_surface`:
```python
IMAGE_CLIENT = {
    "configurable": {
        **CONFIG["configurable"],
        "assistant_ui": {**CONFIG["configurable"]["assistant_ui"],
                         "catalogIds": [*CONFIG["configurable"]["assistant_ui"]["catalogIds"], "image"]},
        "catalog_versions": ["1", "2"],
        "thread_id": "t1",
        "member": {"sub": "sub-noah"},
    }
}
PHOTO = [{"id": "c", "type": "card", "properties": {"title": "Today"}, "children": [
    {"id": "i", "type": "image", "properties": {"imageId": "3f2a9c01", "alt": "navy blazer"}}]}]


async def test_an_image_surface_is_emitted_at_version_2(written, monkeypatch):
    from tests.test_ui_surface import FULL, _row

    async def fake_resolve(ref, member_sub, thread_id, *, now=None):
        return _row()

    monkeypatch.setattr("eve.ui.surface.store.resolve", fake_resolve)
    result = await tools.show_surface.ainvoke(
        {"type": "tool_call", "name": "show_surface", "args": {"components": PHOTO}, "id": "t-img"},
        config=IMAGE_CLIENT,
    )
    operation = result.artifact
    assert operation["surface"]["catalogVersion"] == "2"
    assert operation["surface"]["components"][0]["children"][0]["properties"]["imageId"] == FULL
    assert written[0]["assistant_ui"] == operation


async def test_an_old_client_gets_the_same_surface_as_text_at_version_1(written):
    old = {"configurable": {**CONFIG["configurable"], "thread_id": "t1",
                            "member": {"sub": "sub-noah"}}}
    result = await tools.show_surface.ainvoke(
        {"type": "tool_call", "name": "show_surface", "args": {"components": PHOTO}, "id": "t-old"},
        config=old,
    )
    operation = result.artifact
    assert operation["surface"]["catalogVersion"] == "1"
    assert operation["surface"]["components"][0]["children"][0]["type"] == "text"
```

Run: `uv run pytest tests/test_ui_stream.py tests/test_ui_surface.py tests/test_ui_tools.py -v` → new tests FAIL.

- [ ] **Step 2: Implement**

`stream.py`:
```python
def catalog_versions(config: RunnableConfig | None) -> frozenset[str]:
    """Which surface catalog versions the connected client renders (EVE-21,
    spec 4.2). A client from before versioning sends nothing and means "1".
    Unknown versions are dropped: advertising "9" must not make this server
    stamp something it cannot validate."""
    declared = ((config or {}).get("configurable") or {}).get("catalog_versions")
    known = set(protocol.CATALOG_VERSIONS)
    advertised = set(declared) & known if isinstance(declared, list) else set()
    return frozenset(advertised | {protocol.CATALOG_VERSION})
```

`surface.py` — add `import copy`, `import logging`, `from eve.context import principal_sub`, `from eve.images import store`, `from eve.ui import stream` (check for an import cycle: `eve.ui.stream` imports only `protocol`, so this is safe). Then:

```python
logger = logging.getLogger(__name__)


def aspect_of(width: int, height: int) -> str:
    ratio = width / height if height else 1.0
    if ratio > 1.2:
        return "landscape"
    if ratio < 1 / 1.2:
        return "portrait"
    return "square"


def _as_text(node: dict, alt: str) -> dict:
    return {"id": node["id"], "type": "text", "properties": {"text": alt}}


async def prepare_images(components: list, config) -> tuple[list, bool]:
    """Fit every `image` in a model-authored tree to this client and this
    member BEFORE validation (spec 4.1, 4.2).

    Server-side provenance: the client drops an unfetchable image silently,
    so an id this member was never shown in this thread is caught here,
    logged, and shown as its alt text - which is the reason protocol.py
    exists at all. And server-side downgrade: an older phone gets a readable
    text line instead of a dropped surface.
    """
    if "image" not in component_types(components):
        return components, False
    tree = copy.deepcopy(components)
    can_render = protocol.IMAGE_VERSION in stream.catalog_versions(config)
    member_sub = principal_sub(config)
    thread_id = ((config or {}).get("configurable") or {}).get("thread_id")
    shown = False

    async def visit(nodes: list) -> None:
        nonlocal shown
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            props = node.get("properties")
            if node.get("type") == "image" and isinstance(props, dict) and isinstance(props.get("alt"), str):
                if not can_render:
                    nodes[index] = _as_text(node, props["alt"])
                    continue
                row = await store.resolve(str(props.get("imageId", "")), member_sub, thread_id) if member_sub else None
                if row is None:
                    logger.warning(
                        "image %s did not resolve for this member and thread; shown as text",
                        props.get("imageId"),
                    )
                    nodes[index] = _as_text(node, props["alt"])
                    continue
                props["imageId"] = row.id
                props.setdefault("aspect", aspect_of(row.width, row.height))
                shown = True
            children = node.get("children")
            if isinstance(children, list):
                await visit(children)

    await visit(tree)
    return tree, shown
```

`build_create(surface_id, components, *, catalog_version=protocol.CATALOG_VERSION)` — use the argument for `"catalogVersion"`.

`tools.py` `_show_surface` — first lines become:

```python
    components, has_image = await surface.prepare_images(components, config)
    requested = surface.component_types(components)
```

and the build line:

```python
    operation = surface.build_create(
        surface.new_surface_id(),
        components,
        catalog_version=protocol.IMAGE_VERSION if has_image else protocol.CATALOG_VERSION,
    )
```

`stream.supports` is unchanged: the client keeps declaring `catalogVersion: "1"` in `assistant_ui` (the baseline), with `image` in `catalogIds` — Task 17 explains why.

- [ ] **Step 3: Run the UI suite**

Run: `uv run pytest tests/test_ui_stream.py tests/test_ui_surface.py tests/test_ui_tools.py tests/test_ui_persist.py tests/test_graph.py -v` → all pass.

- [ ] **Step 4: Persisted-frame bound** — append to `tests/test_ui_persist.py`:

```python
def test_a_persisted_image_costs_its_id_and_alt_not_its_pixels():
    from eve.ui import protocol

    op = {"protocol": protocol.PROTOCOL, "op": "create", "surface": {
        "surfaceId": "sf-1", "catalogId": "column", "catalogVersion": "2",
        "components": [{"id": "i", "type": "image", "properties": {
            "imageId": "3f2a9c01-0000-4000-8000-000000000001",
            "alt": "navy blazer", "aspect": "portrait"}}],
        "data": {}, "localState": {}}}
    assert protocol.validate_operation(op) is None
    image_node = protocol._compact(op["surface"]["components"][0])
    assert len(image_node.encode()) < 160
```

Run: `uv run pytest tests/test_ui_persist.py -v` → pass.

- [ ] **Step 5: Commit**

```bash
git add src/eve/ui/stream.py src/eve/ui/surface.py src/eve/ui/tools.py tests/test_ui_*.py
git commit -m "feat(ui): stamp image surfaces v2, downgrade for old clients, check provenance (EVE-21)"
```

---

### Task 13: `eve.images.from_immich`

**Files:**
- Create: `src/eve/images/immich.py`
- Test: `tests/test_images_immich.py`

**Interfaces:**
```python
async def from_immich(member_sub: str, asset_id: str, *, thread_id: str | None) -> str | None
```

- [ ] **Step 1: Failing tests**

```python
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
```

Run: `uv run pytest tests/test_images_immich.py -v` → FAIL (`ImportError`).

- [ ] **Step 2: Implement `src/eve/images/immich.py`**

```python
"""Immich photographs into `eve_image`, as a one-day cache (spec 2.4, 3.4).

Returns the image id or None - never a broken id. A specialist still returns
a string; it cites what this returns as `[image <short-id>]`, and Eve places
that id in a surface. The Immich API key stays in eve-tools.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging

from starlette.concurrency import run_in_threadpool

from eve.images import store
from eve.images.process import normalise
from eve.tools_client import invoke

logger = logging.getLogger(__name__)


async def from_immich(member_sub: str, asset_id: str, *, thread_id: str | None) -> str | None:
    raw = await invoke("immich.asset_image", {"asset_id": asset_id}, timeout=30.0)
    if raw.startswith("error:"):
        logger.warning("immich preview for %s unavailable: %s", asset_id, raw)
        return None
    try:
        data = base64.b64decode(json.loads(raw)["base64"], validate=True)
        image = await run_in_threadpool(normalise, data)
    except (ValueError, KeyError, TypeError, binascii.Error):
        logger.warning("immich preview for %s was not a usable image", asset_id)
        return None
    row = await store.put(
        member_sub, image, origin="immich", thread_id=thread_id, source_ref=asset_id
    )
    return row.id
```

- [ ] **Step 3: Run** `uv run pytest tests/test_images_immich.py -v` → 3 passed.

- [ ] **Step 4: Commit**

```bash
git add src/eve/images/immich.py tests/test_images_immich.py
git commit -m "feat(images): cache Immich previews as image ids (EVE-21)"
```

---

### Task 14: The stylist can show a garment

The catalogue is text and stays text (it is the whole wardrobe in every prompt; adding 36-character asset ids to 200 lines would cost ~2k tokens per call). The new tool looks a garment up by the exact name the catalogue already uses.

**Files:**
- Modify: `src/eve/specialists/stylist.py`, `prompts/stylist.md`
- Test: `tests/test_specialists_stylist.py`

**Interfaces:**
```python
@tool
async def photo_of(garment: str, config: RunnableConfig) -> str
    # "[image 3f2a9c01] navy blazer" | a plain sentence explaining why not
```

- [ ] **Step 1: Failing tests** — append to `tests/test_specialists_stylist.py`:

```python
async def test_photo_of_caches_the_garments_photo_and_cites_its_short_id(monkeypatch):
    calls = {}

    async def fake_list(member_sub):
        return [{"name": "Navy blazer", "asset_id": "a-7", "category": "outerwear"}]

    async def fake_from_immich(member_sub, asset_id, *, thread_id):
        calls["args"] = (member_sub, asset_id, thread_id)
        return "3f2a9c01-0000-4000-8000-000000000001"

    monkeypatch.setattr(stylist_module.wardrobe_store, "list_items", fake_list)
    monkeypatch.setattr(stylist_module, "from_immich", fake_from_immich)
    config = {"configurable": {**CONFIG["configurable"], "thread_id": "t1"}}

    result = await stylist_module.photo_of.ainvoke({"garment": "navy blazer"}, config=config)

    assert result == "[image 3f2a9c01] Navy blazer"
    assert calls["args"] == ("sub-noah", "a-7", "t1")


async def test_photo_of_an_unknown_garment_says_so(monkeypatch):
    async def fake_list(member_sub):
        return [{"name": "Navy blazer", "asset_id": "a-7", "category": "outerwear"}]

    monkeypatch.setattr(stylist_module.wardrobe_store, "list_items", fake_list)
    result = await stylist_module.photo_of.ainvoke({"garment": "red scarf"}, config=CONFIG)
    assert "no garment called" in result.lower()


async def test_photo_of_when_immich_is_down_says_so(monkeypatch):
    async def fake_list(member_sub):
        return [{"name": "Navy blazer", "asset_id": "a-7", "category": "outerwear"}]

    async def fake_from_immich(*_a, **_k):
        return None

    monkeypatch.setattr(stylist_module.wardrobe_store, "list_items", fake_list)
    monkeypatch.setattr(stylist_module, "from_immich", fake_from_immich)
    result = await stylist_module.photo_of.ainvoke({"garment": "Navy blazer"}, config=CONFIG)
    assert "photo" in result.lower() and "[image" not in result


def test_the_stylist_is_given_photo_of():
    import inspect

    source = inspect.getsource(stylist_module)
    assert "photo_of" in source.split("ask_stylist = build_specialist(")[1]
```

Run: `uv run pytest tests/test_specialists_stylist.py -v` → FAIL.

- [ ] **Step 2: Implement** in `src/eve/specialists/stylist.py`:

Imports: `from eve.images import store as image_store`, `from eve.images.immich import from_immich`, `from eve.wardrobe import store as wardrobe_store`.

```python
@tool
async def photo_of(garment: str, config: RunnableConfig) -> str:
    """Get a photo of one garment from the wardrobe, to show the member.

    `garment` is the name exactly as `read_wardrobe` lists it. Returns
    `[image <id>] <name>`; put that `[image <id>]` in your answer so Eve can
    show it. Call it only for garments you are recommending.
    """
    member = _member(config)
    wanted = garment.strip().lower()
    items = await wardrobe_store.list_items(member["sub"])
    match = next((i for i in items if i["name"].strip().lower() == wanted), None)
    if match is None:
        return f"There is no garment called {garment!r} in the wardrobe."
    thread_id = (config.get("configurable") or {}).get("thread_id")
    image_id = await from_immich(member["sub"], match["asset_id"], thread_id=thread_id)
    if image_id is None:
        return f"The photo of {match['name']} could not be fetched right now."
    return f"[image {image_store.short_id(image_id)}] {match['name']}"
```

Add `photo_of` to `ask_stylist`'s `tools=[...]` list. Update the module docstring's "no image ever enters this loop" sentence: images still never enter as pixels until Phase 3; `photo_of` only mints an id.

- [ ] **Step 3: Prompt** — in `prompts/stylist.md`, replace the sentence "The member cannot see a photograph of what you are describing — they will go to the wardrobe and look for it." with:

```markdown
They will go to the wardrobe and look for what you name. For each garment you
recommend, call `photo_of` with its catalogue name and put the `[image …]` it
returns next to that garment in your answer, so Eve can show it. If a photo
cannot be fetched, recommend it anyway by name.
```

- [ ] **Step 4: Run** `uv run pytest tests/test_specialists_stylist.py tests/test_specialists_base.py -v` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/eve/specialists/stylist.py prompts/stylist.md tests/test_specialists_stylist.py
git commit -m "feat(stylist): cite garment photos as image ids (EVE-21)"
```

**Server half of phase 2 done.** Run `uv run pytest -q` → green except the `acp` baseline. The server is safe to deploy ahead of the phone: an old phone never advertises `"2"`, so every image downgrades to its alt text.

---
### Task 15: Flutter — protocol validator and capabilities learn `image`

All Flutter tasks: `cd ~/GitHub/open-assistant/flutter-open-assistant`. First time only: `git fetch && git switch -c eve-21-images origin/main`.

**Why `assistant_ui.catalogVersion` stays `"1"`:** the server's `stream.supports` compares it to its baseline. Declaring `"2"` there would make every *current* server refuse every surface. Versions a client can render travel separately in `config.configurable.catalog_versions` (Task 17); `image` goes into `catalogIds` so the server's schema offers it.

**Files:**
- Modify: `lib/data/services/agent/dynamic_surface_protocol.dart`, `lib/domain/models/dynamic_ui/dynamic_ui_capabilities.dart`, `lib/ui/features/chat/dynamic_ui/dynamic_surface_catalog.dart`, `lib/data/services/dynamic_ui/dynamic_surface_cache.dart`
- Test: `test/data/services/agent/dynamic_surface_protocol_test.dart`, `test/data/services/agent/agent_service_test.dart`, `test/ui/features/chat/dynamic_ui/dynamic_surface_catalog_test.dart`

**Interfaces (Dart):**
```dart
// DynamicSurfaceProtocol
static const catalogVersion = '1';                       // baseline, unchanged
static const catalogVersions = <String, Set<String>>{'1': _v1, '2': {..._v1, 'image'}};
// DynamicUiCapabilities
static const v1 = ...catalogIds now includes 'image'...;
static const catalogVersions = ['1', '2'];               // sent as configurable.catalog_versions
// DynamicSurfaceCatalog
static const versions = {'1', '2'};
static bool supports(DynamicSurfaceDefinition s) => versions.contains(s.catalogVersion) && ids.contains(s.catalogId);
```

- [ ] **Step 1: Failing tests** — append to `dynamic_surface_protocol_test.dart`, reusing the `create(...)` helper near line 590 but with a version parameter (add `String version = '1'` to that helper and use it for `'catalogVersion'`):

```dart
  group('image (catalog version 2)', () {
    const id = '3f2a9c01-0000-4000-8000-000000000001';
    Map<String, Object?> image([Map<String, Object?> extra = const {}]) => {
      'id': 'i1',
      'type': 'image',
      'properties': {'imageId': id, 'alt': 'navy blazer', ...extra},
    };

    test('is valid in a version 2 surface', () {
      expect(
        DynamicSurfaceProtocol.validateOperation(
          create([image({'aspect': 'portrait'})], version: '2'),
        ),
        isNull,
      );
    });

    test('is a component-type error in a version 1 surface', () {
      expect(
        DynamicSurfaceProtocol.validateOperation(create([image()])),
        'component-type',
      );
    });

    test('a version 2 surface without images is valid', () {
      expect(
        DynamicSurfaceProtocol.validateOperation(create([
          {'id': 't', 'type': 'text', 'properties': {'text': 'hi'}},
        ], version: '2')),
        isNull,
      );
    });

    test('requires imageId and alt', () {
      for (final key in ['imageId', 'alt']) {
        final component = image();
        (component['properties']! as Map).remove(key);
        expect(
          DynamicSurfaceProtocol.validateOperation(create([component], version: '2')),
          'component-schema',
          reason: key,
        );
      }
    });

    test('imageId is a UUID, never a URL', () {
      for (final bad in ['3f2a9c01', 'https://evil.example/x.jpg', '']) {
        expect(
          DynamicSurfaceProtocol.validateOperation(
            create([image({'imageId': bad})], version: '2'),
          ),
          'component-schema',
          reason: bad,
        );
      }
    });

    test('aspect is a closed set', () {
      expect(
        DynamicSurfaceProtocol.validateOperation(
          create([image({'aspect': 'wide'})], version: '2'),
        ),
        'component-schema',
      );
    });

    test('is never legal in a widget snapshot', () {
      final surface = DynamicSurfaceDefinition.fromJson({
        'surfaceId': 's', 'catalogId': 'column', 'catalogVersion': '2',
        'components': [image()],
      });
      expect(
        DynamicSurfaceProtocol.validateSurface(surface, widget: true),
        'component-type',
      );
    });

    test('an unknown version is still rejected', () {
      expect(
        DynamicSurfaceProtocol.validateOperation(create([image()], version: '3')),
        'catalog-version',
      );
    });
  });
```

In `agent_service_test.dart`'s v1 capabilities test, append `'image'` to the expected `catalogIds` list, and add:

```dart
  test('advertises the catalog versions it renders', () {
    expect(DynamicUiCapabilities.catalogVersions, ['1', '2']);
  });
```

In `dynamic_surface_catalog_test.dart`: add `'image'` to the exact-ids expectation and

```dart
    test('accepts version 2', () {
      expect(DynamicSurfaceCatalog.supports(surface(catalogVersion: '2')), isTrue);
    });
```

Run: `flutter test test/data/services/agent/dynamic_surface_protocol_test.dart test/data/services/agent/agent_service_test.dart test/ui/features/chat/dynamic_ui/dynamic_surface_catalog_test.dart` → new tests FAIL.

- [ ] **Step 2: Implement**

`dynamic_surface_protocol.dart`:
- Rename `_componentTypes` → `_v1Types`; add
  ```dart
  /// EVE-21: version 2 is version 1 plus `image`. Mirrors eve-ai's
  /// `eve.ui.protocol.CATALOG_VERSIONS`; keep the two in lockstep.
  static const catalogVersions = <String, Set<String>>{
    '1': _v1Types,
    '2': {..._v1Types, 'image'},
  };
  static final _imageId = RegExp(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
  );
  static const _aspects = {'square', 'portrait', 'landscape'};
  ```
- `validateSurface`: replace the version check and catalog check with
  ```dart
  final allowed = catalogVersions[surface.catalogVersion];
  if (allowed == null) return 'catalog-version';
  if (!_v1Types.contains(surface.catalogId)) return 'catalog';
  final componentError = _validateComponents(surface.components, widget: widget, allowed: allowed);
  ```
- `_validateComponents(..., {bool widget = false, Set<String> allowed = const {..._v1Types, 'image'}})` — Dart const-set spread is legal; if the analyzer objects, use a `static final _allTypes`. Check `allowed.contains(component.type)`, then `if (widget && component.type == 'image') return 'component-type';`.
- `_validateComponentProperties`: add `'image' => {'imageId', 'alt', 'aspect'},` to the `allowed` switch; in the per-key switch add
  ```dart
  'imageId' => entry.value is String && _imageId.hasMatch(entry.value! as String)
      ? null : 'component-schema',
  'alt' => entry.value is String && (entry.value! as String).trim().isNotEmpty
      ? validateJsonValue(entry.value) : 'component-schema',
  'aspect' => _aspects.contains(entry.value) ? null : 'component-schema',
  ```
  and after the button rule
  ```dart
  if (component.type == 'image' &&
      !(component.properties.containsKey('imageId') &&
          component.properties.containsKey('alt'))) {
    return 'component-schema';
  }
  ```

`dynamic_ui_capabilities.dart`: append `'image'` to `v1.catalogIds`, and add
```dart
  /// Surface catalog versions this build renders. Sent as
  /// `config.configurable.catalog_versions`, NOT in [catalogVersion] -
  /// that field is the server's baseline gate and must stay '1' so a server
  /// that predates versioning keeps rendering (eve-ai EVE-21, spec 4.2).
  static const catalogVersions = ['1', '2'];
```

`dynamic_surface_catalog.dart`: add `'image'` to `ids`, replace `version` with `static const versions = {'1', '2'};`, and `supports` becomes `versions.contains(surface.catalogVersion) && ids.contains(surface.catalogId)`. Update the lockstep comment to mention `catalogVersions`.

`dynamic_surface_cache.dart`: the cache stamps `cachedCatalogVersion: DynamicSurfaceProtocol.catalogVersion` and refuses others. Leave the stamp alone — it records the *validator generation*, and `validateSurface` on read already accepts both versions. No change needed; add one test only if `flutter analyze` flags the renamed constant.

- [ ] **Step 3: Run**

```bash
flutter analyze && flutter test test/data/services/agent test/ui/features/chat/dynamic_ui test/data/services/dynamic_ui
```
Expected: no analyzer issues; all pass.

- [ ] **Step 4: Commit**

```bash
git add lib test
git commit -m "feat(dynamic-ui): image component in catalog version 2 (EVE-21)"
```

---

### Task 16: Flutter — authenticated image loading and the `image` renderer

**Files:**
- Create: `lib/data/services/agent/image_loader.dart`, `lib/ui/features/chat/dynamic_ui/eve_image.dart`
- Modify: `lib/data/services/agent/langgraph_client.dart`, `lib/data/services/agent/agent_service.dart`, `lib/data/services/agent/langgraph_agent_service.dart`, `lib/data/repositories/agent_repository.dart`, `lib/ui/features/chat/dynamic_ui/dynamic_surface_renderer.dart`, `lib/ui/features/chat/views/chat_message.dart`, `lib/ui/features/chat/view_models/conversation_view_model.dart`, `lib/domain/use_cases/assistant_session_use_case.dart`
- Test: `test/data/services/agent/langgraph_client_test.dart`, `test/data/services/agent/image_loader_test.dart`, `test/ui/features/chat/dynamic_ui/eve_image_test.dart`, `test/ui/features/chat/dynamic_ui/dynamic_surface_renderer_test.dart`

**Interfaces (Dart):**
```dart
// langgraph_client.dart
Future<http.Response> getBytes(String path);      // authed GET, no status check
Future<Object?> postMultipart(String path, {required String field,
    required String filePath, Map<String, String> fields = const {}});  // throws LangGraphException on non-2xx

// image_loader.dart
sealed class ImageLoadResult {}
final class ImageLoaded extends ImageLoadResult { final Uint8List bytes; }
final class ImageUnavailable extends ImageLoadResult { final bool expired; }   // 404/410/network
abstract interface class ImageLoader { Future<ImageLoadResult> load(String imageId); }
class LangGraphImageLoader implements ImageLoader { LangGraphImageLoader(LangGraphClient c); }
// in-memory LRU of 32 entries keyed by id; a failed load is not cached

// AgentService / AgentRepository
ImageLoader? get images => null;   // LangGraphAgentService overrides

// eve_image.dart
class EveImage extends StatelessWidget {
  const EveImage({required this.imageId, required this.alt, this.aspect, required this.loader});
}
```

The loader is threaded into the renderer as an optional constructor parameter (`ImageLoader? imageLoader`), passed from `chat_message.dart` via `provider.imageLoader` (`ConversationViewModel` → use case → `AgentRepository.images`). A `null` loader renders the unavailable tile — surfaces in widget cards never contain images anyway.

- [ ] **Step 1: Failing client tests** — append to `langgraph_client_test.dart`:

```dart
  group('image transport', () {
    test('getBytes sends the bearer and returns the raw response', () async {
      late http.Request seen;
      final client = LangGraphClient(
        baseUrl: Uri.parse('https://eve.test/api'),
        apiKey: 'k',
        httpClient: MockClient((request) async {
          seen = request;
          return http.Response.bytes([1, 2, 3], 200, headers: {'content-type': 'image/jpeg'});
        }),
      );
      final response = await client.getBytes('/images/abc');
      expect(seen.url.toString(), 'https://eve.test/api/images/abc');
      expect(seen.headers['authorization'], 'Bearer k');
      expect(response.bodyBytes, [1, 2, 3]);
    });

    test('postMultipart uploads the file field and decodes the JSON answer', () async {
      final dir = await Directory.systemTemp.createTemp();
      final file = File('${dir.path}/p.jpg')..writeAsBytesSync([9, 9, 9]);
      late http.BaseRequest seen;
      final client = LangGraphClient(
        baseUrl: Uri.parse('https://eve.test'),
        apiKey: 'k',
        httpClient: MockClient.streaming((request, body) async {
          seen = request;
          await body.drain<void>();
          return http.StreamedResponse(
            Stream.value(utf8.encode('{"image_id":"x","width":1,"height":1}')), 200);
        }),
      );
      final result = await client.postMultipart('/images',
          field: 'file', filePath: file.path, fields: {'thread_id': 't1'});
      expect(seen.url.path, '/images');
      expect(seen.headers['authorization'], 'Bearer k');
      expect(seen.headers['content-type'], startsWith('multipart/form-data'));
      expect((result as Map)['image_id'], 'x');
    });

    test('postMultipart throws LangGraphException on 415', () async {
      final dir = await Directory.systemTemp.createTemp();
      final file = File('${dir.path}/x.pdf')..writeAsBytesSync([1]);
      final client = LangGraphClient(
        baseUrl: Uri.parse('https://eve.test'),
        apiKey: '',
        httpClient: MockClient.streaming((request, body) async {
          await body.drain<void>();
          return http.StreamedResponse(Stream.value(utf8.encode('{"detail":"not an image"}')), 415);
        }),
      );
      expect(
        () => client.postMultipart('/images', field: 'file', filePath: file.path),
        throwsA(isA<LangGraphException>().having((e) => e.statusCode, 'status', 415)),
      );
    });
  });
```

(Add `import 'dart:io';` to the test.)

Run: `flutter test test/data/services/agent/langgraph_client_test.dart` → FAIL.

- [ ] **Step 2: Implement client methods** in `langgraph_client.dart`:

```dart
  /// Authenticated GET returning the raw response - the image proxy
  /// (`GET /images/{id}`) answers bytes, and the caller needs 404 vs 410.
  Future<http.Response> getBytes(String path) => _http.get(
    _resolve(path),
    headers: {
      if (_apiKey.isNotEmpty) 'x-api-key': _apiKey,
      if (_apiKey.isNotEmpty) 'authorization': 'Bearer $_apiKey',
    },
  );

  /// Multipart upload (`POST /images`). Same auth headers as every other
  /// call; the content-type is the multipart boundary `http` generates.
  Future<Object?> postMultipart(
    String path, {
    required String field,
    required String filePath,
    Map<String, String> fields = const {},
  }) async {
    final request = http.MultipartRequest('POST', _resolve(path))
      ..fields.addAll(fields)
      ..files.add(await http.MultipartFile.fromPath(field, filePath));
    if (_apiKey.isNotEmpty) {
      request.headers['x-api-key'] = _apiKey;
      request.headers['authorization'] = 'Bearer $_apiKey';
    }
    final response = await http.Response.fromStream(await _http.send(request));
    if (!_isOk(response.statusCode)) {
      throw LangGraphException(
        response.statusCode,
        _truncate(response.body),
        rawBody: response.body,
      );
    }
    return decodeJson(response.body);
  }
```

- [ ] **Step 3: Failing loader test** — `test/data/services/agent/image_loader_test.dart`:

```dart
import 'dart:typed_data';

import 'package:assistant/data/services/agent/image_loader.dart';
import 'package:assistant/data/services/agent/langgraph_client.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

LangGraphClient _client(Map<String, int> statuses, List<String> seen) => LangGraphClient(
  baseUrl: Uri.parse('https://eve.test'),
  apiKey: 'k',
  httpClient: MockClient((request) async {
    seen.add(request.url.path);
    final status = statuses[request.url.path] ?? 200;
    return http.Response.bytes(status == 200 ? [1, 2] : [], status);
  }),
);

void main() {
  test('loads, then serves the second request from memory', () async {
    final seen = <String>[];
    final loader = LangGraphImageLoader(_client({}, seen));
    final first = await loader.load('a');
    final second = await loader.load('a');
    expect((first as ImageLoaded).bytes, Uint8List.fromList([1, 2]));
    expect(second, isA<ImageLoaded>());
    expect(seen, ['/images/a']);
  });

  test('410 is unavailable-and-expired, 404 is unavailable', () async {
    final loader = LangGraphImageLoader(
      _client({'/images/old': 410, '/images/gone': 404}, []),
    );
    expect((await loader.load('old') as ImageUnavailable).expired, isTrue);
    expect((await loader.load('gone') as ImageUnavailable).expired, isFalse);
  });

  test('a failure is not cached', () async {
    final seen = <String>[];
    final statuses = {'/images/a': 500};
    final loader = LangGraphImageLoader(_client(statuses, seen));
    await loader.load('a');
    statuses.remove('/images/a');
    expect(await loader.load('a'), isA<ImageLoaded>());
    expect(seen.length, 2);
  });

  test('a network error is unavailable, never a throw', () async {
    final loader = LangGraphImageLoader(LangGraphClient(
      baseUrl: Uri.parse('https://eve.test'),
      apiKey: '',
      httpClient: MockClient((_) async => throw http.ClientException('offline')),
    ));
    expect(await loader.load('a'), isA<ImageUnavailable>());
  });
}
```

- [ ] **Step 4: Implement `lib/data/services/agent/image_loader.dart`**

```dart
import 'dart:collection';
import 'dart:typed_data';

import 'package:assistant/core/logging.dart'; // use whatever `log` import langgraph_agent_service.dart uses
import 'package:assistant/data/services/agent/langgraph_client.dart';

/// Fetches Eve-hosted images (`GET /images/{id}`) with the provider's bearer.
/// The app never fetches a model-chosen URL: a surface carries only an id,
/// and this is the only place that id becomes bytes (eve-ai EVE-21, 4.1).
sealed class ImageLoadResult {
  const ImageLoadResult();
}

final class ImageLoaded extends ImageLoadResult {
  const ImageLoaded(this.bytes);
  final Uint8List bytes;
}

final class ImageUnavailable extends ImageLoadResult {
  const ImageUnavailable({this.expired = false});

  /// 410: retention removed it. The tile says "no longer available" rather
  /// than implying something is broken.
  final bool expired;
}

abstract interface class ImageLoader {
  Future<ImageLoadResult> load(String imageId);
}

class LangGraphImageLoader implements ImageLoader {
  LangGraphImageLoader(this._client);

  final LangGraphClient _client;
  // ponytail: 32 decoded-size-bounded (~1 MB each) images is plenty for a
  // chat scroll; no disk cache until someone notices a re-fetch.
  static const _capacity = 32;
  final _cache = LinkedHashMap<String, Uint8List>();

  @override
  Future<ImageLoadResult> load(String imageId) async {
    final hit = _cache.remove(imageId);
    if (hit != null) {
      _cache[imageId] = hit;
      return ImageLoaded(hit);
    }
    try {
      final response = await _client.getBytes('/images/${Uri.encodeComponent(imageId)}');
      if (response.statusCode == 200) {
        _cache[imageId] = response.bodyBytes;
        if (_cache.length > _capacity) _cache.remove(_cache.keys.first);
        return ImageLoaded(response.bodyBytes);
      }
      return ImageUnavailable(expired: response.statusCode == 410);
    } catch (error) {
      log.warning('image $imageId failed to load: $error');
      return const ImageUnavailable();
    }
  }
}
```

(Before writing, check how `langgraph_agent_service.dart` imports `log` and copy that import exactly.)

- [ ] **Step 5: Failing widget test** — `test/ui/features/chat/dynamic_ui/eve_image_test.dart`:

```dart
import 'dart:async';
import 'dart:typed_data';

import 'package:assistant/data/services/agent/image_loader.dart';
import 'package:assistant/ui/features/chat/dynamic_ui/eve_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../../../../helpers/pump_app.dart';

// A 1x1 transparent PNG.
final _png = Uint8List.fromList(const [
  0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48,
  0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00,
  0x00, 0x1F, 0x15, 0xC4, 0x89, 0x00, 0x00, 0x00, 0x0A, 0x49, 0x44, 0x41, 0x54, 0x78,
  0x9C, 0x63, 0x00, 0x01, 0x00, 0x00, 0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00,
  0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
]);

class _Loader implements ImageLoader {
  _Loader(this.result);
  final Completer<ImageLoadResult> result;
  @override
  Future<ImageLoadResult> load(String imageId) => result.future;
}

void main() {
  testWidgets('shows a sized placeholder, then the image', (tester) async {
    final done = Completer<ImageLoadResult>();
    await pumpApp(tester, Material(child: EveImage(
      imageId: 'a', alt: 'navy blazer', aspect: 'portrait', loader: _Loader(done))));
    expect(find.byType(Image), findsNothing);
    expect(find.bySemanticsLabel('navy blazer'), findsOneWidget);
    final box = tester.getSize(find.byKey(const ValueKey('eve-image:a')));
    expect(box.height, greaterThan(box.width)); // portrait placeholder

    done.complete(ImageLoaded(_png));
    await tester.pumpAndSettle();
    expect(find.byType(Image), findsOneWidget);
  });

  testWidgets('an expired image says so and keeps the alt', (tester) async {
    final done = Completer<ImageLoadResult>()..complete(const ImageUnavailable(expired: true));
    await pumpApp(tester, Material(child: EveImage(
      imageId: 'a', alt: 'navy blazer', loader: _Loader(done))));
    await tester.pumpAndSettle();
    expect(find.text('Image no longer available'), findsOneWidget);
    expect(find.text('navy blazer'), findsOneWidget);
  });

  testWidgets('no loader renders the unavailable tile', (tester) async {
    await pumpApp(tester, const Material(child: EveImage(
      imageId: 'a', alt: 'navy blazer', loader: null)));
    await tester.pumpAndSettle();
    expect(find.text("Image couldn't be loaded"), findsOneWidget);
  });
}
```

- [ ] **Step 6: Implement `lib/ui/features/chat/dynamic_ui/eve_image.dart`**

```dart
import 'package:app_ui/app_ui.dart';
import 'package:assistant/data/services/agent/image_loader.dart';
import 'package:flutter/material.dart';

/// The `image` catalog component (catalog version 2, eve-ai EVE-21 4.1).
/// Bytes come only from [loader] - Eve's own authenticated proxy - so there
/// is no URL anywhere in a surface for a model to choose.
class EveImage extends StatelessWidget {
  const EveImage({
    super.key,
    required this.imageId,
    required this.alt,
    this.aspect,
    required this.loader,
  });

  final String imageId;
  final String alt;
  final String? aspect;
  final ImageLoader? loader;

  double get _ratio => switch (aspect) {
    'portrait' => 3 / 4,
    'landscape' => 4 / 3,
    _ => 1,
  };

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    return Semantics(
      image: true,
      label: alt,
      child: ClipRRect(
        key: ValueKey('eve-image:$imageId'),
        borderRadius: AppRadii.cardMd,
        child: AspectRatio(
          aspectRatio: _ratio,
          child: loader == null
              ? _Unavailable(alt: alt, expired: false)
              : FutureBuilder<ImageLoadResult>(
                  future: loader!.load(imageId),
                  builder: (context, snapshot) => switch (snapshot.data) {
                    null => ColoredBox(color: colors.stroke),
                    ImageLoaded(:final bytes) => Image.memory(
                      bytes,
                      fit: BoxFit.cover,
                      gaplessPlayback: true,
                      semanticLabel: alt,
                      errorBuilder: (_, _, _) => _Unavailable(alt: alt, expired: false),
                    ),
                    ImageUnavailable(:final expired) => _Unavailable(alt: alt, expired: expired),
                  },
                ),
        ),
      ),
    );
  }
}

class _Unavailable extends StatelessWidget {
  const _Unavailable({required this.alt, required this.expired});

  final String alt;
  final bool expired;

  @override
  Widget build(BuildContext context) {
    final colors = context.colors;
    final type = context.type;
    return ColoredBox(
      color: colors.raised,
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Text(
              expired ? 'Image no longer available' : "Image couldn't be loaded",
              style: type.uiSm.copyWith(color: colors.ink3),
            ),
            const SizedBox(height: AppSpacing.xs),
            Text(alt, style: type.uiSm.copyWith(color: colors.ink2), textAlign: TextAlign.center),
          ],
        ),
      ),
    );
  }
}
```

`FutureBuilder` recreating its future on every rebuild is fine here because the loader's cache answers the second call synchronously-ish; if the widget test shows flicker, convert to a `StatefulWidget` that loads in `initState`. Check `colors.ink2` exists in `app_ui` (grep `ink2` in `packages/app_ui`); use `ink3` if not.

- [ ] **Step 7: Renderer wiring** — `dynamic_surface_renderer.dart`:
- constructor gains `this.imageLoader` (`final ImageLoader? imageLoader;`);
- thread it into `_build` as a new trailing parameter `ImageLoader? images`;
- add
  ```dart
      case 'image':
        return EveImage(
          imageId: component.properties['imageId']! as String,
          alt: component.properties['alt']! as String,
          aspect: component.properties['aspect'] as String?,
          loader: images,
        );
  ```
  (validation has already guaranteed both strings exist).

Append a renderer test to `dynamic_surface_renderer_test.dart` that pumps a `catalogVersion: '2'` surface with one image and a fake loader completing `ImageLoaded`, and asserts `find.byType(EveImage)` finds one.

- [ ] **Step 8: Plumb the loader**
- `agent_service.dart`: `ImageLoader? get images => null;` beside `widgets`/`routines` (with a one-line doc).
- `langgraph_agent_service.dart`: `late final ImageLoader _images = LangGraphImageLoader(_client);` and `@override ImageLoader? get images => _images;`.
- `agent_repository.dart`: `ImageLoader? get images => _service.images;`.
- `assistant_session_use_case.dart`: `ImageLoader? get imageLoader => _agentRepo.images;`.
- `conversation_view_model.dart`: `ImageLoader? get imageLoader => _useCase.imageLoader;`.
- `chat_message.dart` line ~251: pass `imageLoader: provider.imageLoader,` to `DynamicSurfaceRenderer`.

- [ ] **Step 9: Run**

```bash
flutter analyze && flutter test test/data/services/agent test/ui/features/chat test/data/repositories
```
Expected: clean; all pass.

- [ ] **Step 10: Commit**

```bash
git add lib test
git commit -m "feat(chat): render Eve-hosted images through an authenticated loader (EVE-21)"
```

---

### Task 17: Flutter — advertise catalog version 2 on every run

**Files:**
- Modify: `lib/data/services/agent/langgraph_agent_service.dart`
- Test: `test/data/services/agent/langgraph_agent_service_test.dart`

- [ ] **Step 1: Failing test** — replace the expectation in `sends capabilities as assistant_ui run configurable` (line ~249) with:

```dart
      expect(client.runs.single.value['config'], {
        'configurable': {
          'assistant_ui': DynamicUiCapabilities.v1.toJson(),
          'catalog_versions': DynamicUiCapabilities.catalogVersions,
        },
      });
```

Run: `flutter test test/data/services/agent/langgraph_agent_service_test.dart` → FAIL.

- [ ] **Step 2: Implement** — in `run`, the `config.configurable` map becomes:

```dart
            'configurable': {
              'assistant_ui': capabilities.toJson(),
              // Separate from assistant_ui.catalogVersion on purpose - see
              // DynamicUiCapabilities.catalogVersions.
              'catalog_versions': DynamicUiCapabilities.catalogVersions,
            },
```

- [ ] **Step 3: Run** `flutter test test/data/services/agent/langgraph_agent_service_test.dart` → pass.

- [ ] **Step 4: Commit**

```bash
git add lib test
git commit -m "feat(agent): advertise surface catalog versions to Eve (EVE-21)"
```

**Phase 2 checkpoint:** `flutter analyze && flutter test` (whole suite) in the Flutter repo; `uv run pytest -q` in eve-ai. Then a manual end-to-end check against a dev server (`uv run aegra serve` + app pointed at it): ask "what should I wear today?" and confirm the stylist's garment photo renders in a card. Ship server first, then the app.

---
# Phase 3: Images in (server, then the coordinated Flutter release)

### Task 18: Human text readers survive a content list

Once the phone sends `[{"type":"text"}, {"type":"eve_image"}]`, every `str(human.content)` in the graph turns into a Python list repr: recall queries, memory extraction, titles, and `may_author`. `state._text_of` already does the right flattening, so make it public and route those readers through it.

**Files:**
- Modify: `src/eve/state.py` (rename `_text_of` to `text_of`, and keep a `_text_of = text_of` alias only if something outside `state.py` imports it; grep first).
- Modify: `src/eve/memory/recall.py` (`_last_human_text`), `src/eve/memory/extract.py` (`last_exchange`, `_visible_content`), `src/eve/title.py` (`_first_exchange`), `src/eve/skills/authoring.py` (the `human = next(...)` line).
- Test: `tests/test_state.py`, `tests/test_memory_recall.py`, `tests/test_memory_extract.py`, `tests/test_title.py`. Append to each; find exact file names with `ls tests | grep -E "state|recall|extract|title"`.

**Interfaces:**
```python
def text_of(content) -> str   # str passthrough; list -> concatenated "text" blocks; else ""
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_state.py
def test_text_of_ignores_image_references():
    from eve.state import text_of

    content = [
        {"type": "text", "text": "does this go with navy?"},
        {"type": "eve_image", "image_id": "3f2a9c01-0000-4000-8000-000000000001", "alt": "photo"},
    ]
    assert text_of(content) == "does this go with navy?"
    assert text_of("plain") == "plain"
```

```python
# tests/test_memory_extract.py
def test_last_exchange_reads_text_out_of_a_content_list():
    from langchain_core.messages import AIMessage, HumanMessage

    from eve.memory.extract import last_exchange

    human = HumanMessage(content=[
        {"type": "text", "text": "does this go with navy?"},
        {"type": "eve_image", "image_id": "x", "alt": "photo"},
    ])
    assert last_exchange([human, AIMessage("Yes.")]) == ("does this go with navy?", "Yes.")
```

```python
# tests/test_memory_recall.py
def test_the_recall_query_is_the_text_not_a_list_repr():
    from langchain_core.messages import HumanMessage

    from eve.memory.recall import _last_human_text

    human = HumanMessage(content=[
        {"type": "text", "text": "navy blazer"},
        {"type": "eve_image", "image_id": "x", "alt": "photo"},
    ])
    assert _last_human_text([human]) == "navy blazer"
```

```python
# tests/test_title.py
def test_first_exchange_reads_text_out_of_a_content_list():
    from langchain_core.messages import AIMessage, HumanMessage

    from eve.title import _first_exchange

    human = HumanMessage(content=[
        {"type": "text", "text": "what about this jacket"},
        {"type": "eve_image", "image_id": "x", "alt": "photo"},
    ])
    assert _first_exchange([human, AIMessage("Lovely.")]) == ("what about this jacket", "Lovely.")
```

Run: `uv run pytest tests/test_state.py tests/test_memory_extract.py tests/test_memory_recall.py tests/test_title.py -v`
Expected: the four new tests FAIL.

- [ ] **Step 2: Implement.** Rename the function in `state.py` and update its one internal caller. Then:
- In `recall._last_human_text`, return `text_of(message.content)`.
- In `extract.last_exchange`, `human` becomes `text_of(...)` of the last HumanMessage's content. `_visible_content` keeps its AIMessage branch, and its human branch returns `text_of(message.content)`.
- In `title._first_exchange`, use `text_of(human.content)` in both places it does `str(human.content)`, and `text_of(ai.content)` for the AI side, which also fixes Responses-API list content.
- In `authoring.py`, use `text_of(m.content)` in place of `str(m.content)`.

Import with `from eve.state import text_of` in each file.

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_state.py tests/test_memory_extract.py tests/test_memory_recall.py tests/test_title.py tests/test_skills_authoring.py tests/test_graph.py -v`
Expected: all pass. A test that asserted on list-repr text would be a bug, so update it.

- [ ] **Step 4: Commit**

```bash
git add src/eve tests
git commit -m "fix(state): read human text through text_of so content lists work (EVE-21)"
```

---

### Task 19: `eve.images.hydrate`

**Files:**
- Create: `src/eve/images/hydrate.py`
- Test: `tests/test_images_hydrate.py`

**Interfaces:**
```python
UNAVAILABLE = "[image no longer available]"

def reference(image_id: str, alt: str) -> dict        # {"type": "eve_image", "image_id", "alt"}
def has_references(messages: list) -> bool
async def caption(row: ImageRow) -> str               # cached REFLEX description
async def hydrate(
    messages: list, member_sub: str | None, *,
    native: bool, window: int | None = None,
) -> list
```

Behaviour (§3.2):
- Only `HumanMessage` content lists are touched. Every other message passes through by identity. The input list and its messages are never mutated; changed messages are `model_copy`'d.
- Human turns are counted from the end. References in the last `window` human messages (default `settings.image_hydrate_turns`) are *hydrated*. Older ones become `{"type":"text","text":"[image <short>: <alt>]"}`.
- A hydrated reference is looked up with `store.get(image_id, member_sub)`. `None` (missing, foreign or expired) becomes the `UNAVAILABLE` text block. Otherwise it becomes a label text block `[image <short>]` followed by:
  - `native=True`: `{"type": "image", "base64": b64(row.bytes), "mime_type": row.content_type}`
  - `native=False`: `{"type": "text", "text": "[image <short>: <caption(row)>]"}`, with the label omitted since the caption line carries it.
- `member_sub is None` (no principal) turns every reference into `UNAVAILABLE`, with no store call.
- `caption(row)` returns `row.caption` if set. Otherwise it calls REFLEX once with the image, stores the result via `store.set_caption`, and returns it. If the REFLEX call fails it returns the row's `[image]` alt-free fallback `"a photo"`, and logs.

- [ ] **Step 1: Write the failing tests**

```python
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
```

Run: `uv run pytest tests/test_images_hydrate.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 2: Implement `src/eve/images/hydrate.py`**

```python
"""References to pixels, at the model boundary and nowhere else (spec 3.2).

`EveState` holds `{"type": "eve_image", "image_id", "alt"}` blocks - a few
dozen bytes each, so Aegra's per-turn checkpoint does not grow with image
size. This module rewrites a COPY of the message list right before a model
call. Nothing it returns is ever written back to state.
"""

from __future__ import annotations

import base64
import logging

from langchain_core.messages import HumanMessage

from eve.images import store
from eve.models import Tier, get_model
from eve.settings import get_settings

logger = logging.getLogger(__name__)

UNAVAILABLE = "[image no longer available]"
_CAPTION_PROMPT = (
    "Describe this photo in one sentence for someone who cannot see it. "
    "Name colours, garments, objects and any visible text. No preamble."
)


def reference(image_id: str, alt: str) -> dict:
    return {"type": "eve_image", "image_id": image_id, "alt": alt}


def _is_reference(block: object) -> bool:
    return isinstance(block, dict) and block.get("type") == "eve_image"


def has_references(messages: list) -> bool:
    return any(
        isinstance(m, HumanMessage) and isinstance(m.content, list)
        and any(_is_reference(b) for b in m.content)
        for m in messages
    )


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


async def _describe(row: store.ImageRow) -> str:
    message = HumanMessage(content=[
        _text(_CAPTION_PROMPT),
        {"type": "image", "base64": base64.b64encode(row.bytes).decode(),
         "mime_type": row.content_type},
    ])
    reply = await get_model(Tier.REFLEX).ainvoke([message])
    from eve.state import text_of  # state imports settings only; kept local for clarity

    return text_of(reply.content).strip() or "a photo"


async def caption(row: store.ImageRow) -> str:
    """Generated once per image on REFLEX (Gemini, multimodal since EVE-20's
    wardrobe/vision.py) and cached on the row."""
    if row.caption:
        return row.caption
    try:
        text = await _describe(row)
    except Exception:
        logger.warning("captioning image %s failed", row.id, exc_info=True)
        return "a photo"
    await store.set_caption(row.id, row.member_sub, text)
    return text


async def _expand(block: dict, member_sub: str | None, *, native: bool) -> list[dict]:
    row = await store.get(str(block.get("image_id", "")), member_sub) if member_sub else None
    if row is None:
        return [_text(UNAVAILABLE)]
    short = store.short_id(row.id)
    if not native:
        return [_text(f"[image {short}: {await caption(row)}]")]
    return [
        _text(f"[image {short}]"),
        {"type": "image", "base64": base64.b64encode(row.bytes).decode(),
         "mime_type": row.content_type},
    ]


async def hydrate(
    messages: list,
    member_sub: str | None,
    *,
    native: bool,
    window: int | None = None,
) -> list:
    window = get_settings().image_hydrate_turns if window is None else window
    human_positions = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    recent = set(human_positions[-window:]) if window > 0 else set()

    out = []
    for index, message in enumerate(messages):
        content = message.content if isinstance(message, HumanMessage) else None
        if not isinstance(content, list) or not any(_is_reference(b) for b in content):
            out.append(message)
            continue
        blocks: list = []
        for block in content:
            if not _is_reference(block):
                blocks.append(block)
            elif index in recent:
                blocks.extend(await _expand(block, member_sub, native=native))
            else:
                short = store.short_id(str(block.get("image_id", "")))
                blocks.append(_text(f"[image {short}: {block.get('alt') or 'a photo'}]"))
        out.append(message.model_copy(update={"content": blocks}))
    return out
```

Move the `text_of` import to module top if it causes no cycle. `eve.state` imports `eve.settings`, `eve.memory.types` and `eve.skills.types`, none of which import `eve.images`, so it is safe. Delete the inline comment.

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_images_hydrate.py -v`
Expected: 8 passed.

- [ ] **Step 4: Commit**

```bash
git add src/eve/images/hydrate.py tests/test_images_hydrate.py
git commit -m "feat(images): hydrate references to pixels or captions at the model boundary (EVE-21)"
```

---

### Task 20: Eve's VOICE call sees images, with a caption fallback

**Files:**
- Modify: `src/eve/graph.py`
- Test: `tests/test_graph.py`

**Interfaces:**
```python
async def _for_model(messages: list, config: RunnableConfig, *, native: bool) -> list
    # _stripped_for_model + hydrate; returns input unchanged (no store call) when there are no references
```

Behaviour in the `eve` node:
1. `messages = [_persona_message(prompt), *await _for_model(state["messages"], config, native=TIER_VISION[Tier.VOICE])]`.
2. `try: reply = await bound_model.ainvoke(messages, config)`, then `except openai.BadRequestError` **only when native hydration actually inlined an image**. In that case, log, set span attribute `eve.images.caption_fallback=True`, rebuild with `native=False`, and retry once. Any other exception propagates exactly as today.

Why `BadRequestError`: a LiteLLM fallback hop to a model without vision, or a proxy that rejects `input_image`, answers HTTP 400. Retrying on anything broader would double-bill ordinary failures.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_graph.py`)

```python
IMAGE_ID = "aaaaaaaa-0000-4000-8000-000000000001"


class _RecordingModel(FakeToolCallingModel):
    seen: list = []

    async def ainvoke(self, messages, config=None, **kwargs):
        type(self).seen.append(messages)
        return await super().ainvoke(messages, config, **kwargs)


def _photo_turn():
    return HumanMessage(content=[
        {"type": "text", "text": "does this go with navy?"},
        {"type": "eve_image", "image_id": IMAGE_ID, "alt": "photo sent by Noah"},
    ])


async def test_voice_sees_the_image_and_state_keeps_only_the_reference(monkeypatch):
    from eve.images import hydrate as hydrate_module
    from tests.test_images_hydrate import _row

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")
    monkeypatch.setattr("eve.graph.TIER_VISION", {**__import__("eve.models").models.TIER_VISION, Tier.VOICE: True})

    async def fake_get(image_id, member_sub, *, include_expired=False, now=None):
        return _row(IMAGE_ID)

    monkeypatch.setattr(hydrate_module.store, "get", fake_get)
    _RecordingModel.seen = []
    app = build_graph(
        model_factory=lambda _t: _RecordingModel(messages=iter([AIMessage("Yes.")])),
        recall_fn=_no_recall, extract_fn=_no_extract, suggest_fn=_no_suggest,
    ).compile()

    result = await app.ainvoke({"messages": [_photo_turn()]}, CONFIG)

    sent_human = _RecordingModel.seen[0][1]
    assert any(b.get("type") == "image" for b in sent_human.content)
    stored_human = result["messages"][0]
    assert stored_human.content[1] == {
        "type": "eve_image", "image_id": IMAGE_ID, "alt": "photo sent by Noah"}


async def test_a_rejected_image_retries_once_with_captions(monkeypatch):
    import httpx
    import openai

    from eve.images import hydrate as hydrate_module
    from tests.test_images_hydrate import _row

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")
    monkeypatch.setattr("eve.graph.TIER_VISION", {**__import__("eve.models").models.TIER_VISION, Tier.VOICE: True})

    async def fake_get(image_id, member_sub, *, include_expired=False, now=None):
        return _row(IMAGE_ID, caption="a navy blazer")

    monkeypatch.setattr(hydrate_module.store, "get", fake_get)
    calls = []

    class _PickyModel(FakeToolCallingModel):
        async def ainvoke(self, messages, config=None, **kwargs):
            calls.append(messages)
            if any(isinstance(b, dict) and b.get("type") == "image"
                   for m in messages for b in (m.content if isinstance(m.content, list) else [])):
                request = httpx.Request("POST", "http://litellm/v1/responses")
                raise openai.BadRequestError(
                    "image input not supported",
                    response=httpx.Response(400, request=request), body=None)
            return await super().ainvoke(messages, config, **kwargs)

    app = build_graph(
        model_factory=lambda _t: _PickyModel(messages=iter([AIMessage("It works.")])),
        recall_fn=_no_recall, extract_fn=_no_extract, suggest_fn=_no_suggest,
    ).compile()

    result = await app.ainvoke({"messages": [_photo_turn()]}, CONFIG)

    assert result["messages"][-1].content == "It works."
    assert len(calls) == 2
    assert {"type": "text", "text": "[image aaaaaaaa: a navy blazer]"} in calls[1][1].content


async def test_a_turn_without_images_never_touches_the_image_store(monkeypatch):
    from eve.images import hydrate as hydrate_module

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    async def boom(*_a, **_k):
        raise AssertionError("no store call on a text-only turn")

    monkeypatch.setattr(hydrate_module.store, "get", boom)
    app = build_graph(
        model_factory=_fake_factory, recall_fn=_no_recall,
        extract_fn=_no_extract, suggest_fn=_no_suggest,
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)
    assert result["messages"][-1].content == "Hi Noah."
```

Add `from eve.models import Tier` to the test imports. Replace the `__import__` dance with a clean `from eve.models import TIER_VISION` at the top of the test file. It is written inline above only to keep each test self-contained on the page.

Run: `uv run pytest tests/test_graph.py -v -k "image or captions"`
Expected: FAIL.

- [ ] **Step 2: Implement** in `src/eve/graph.py`:

Imports: `import logging`, `import openai`, `from opentelemetry import trace`, `from eve.context import principal_sub`, `from eve.images.hydrate import has_references, hydrate`, and extend the models import to `from eve.models import TIER_VISION, Tier, get_model`. Add `logger = logging.getLogger(__name__)` if absent.

```python
async def _for_model(messages: list, config: RunnableConfig, *, native: bool) -> list:
    """Everything the VOICE model is shown of the transcript: frames stripped
    (persist_ui), and image references hydrated to pixels or captions
    (EVE-21, spec 3.2). The result is sent and discarded, never written back:
    state keeps only the references, so a checkpoint never holds an image."""
    stripped = _stripped_for_model(messages)
    if not has_references(stripped):
        return stripped
    return await hydrate(stripped, principal_sub(config), native=native)


def _has_pixels(messages: list) -> bool:
    return any(
        isinstance(block, dict) and block.get("type") == "image"
        for m in messages if isinstance(m.content, list) for block in m.content
    )
```

In `eve`, replace the last two lines with:

```python
        persona = _persona_message(prompt)
        messages = [persona, *await _for_model(
            state["messages"], config, native=TIER_VISION[Tier.VOICE])]
        try:
            reply = await bound_model.ainvoke(messages, config)
        except openai.BadRequestError:
            # A LiteLLM fallback hop without vision, or a proxy refusing
            # `input_image`, answers 400. Once, with captions instead of
            # pixels; anything else, and any second failure, propagates as it
            # always has (spec 5).
            if not _has_pixels(messages):
                raise
            logger.warning("VOICE rejected image input; retrying with captions")
            trace.get_current_span().set_attribute("eve.images.caption_fallback", True)
            messages = [persona, *await _for_model(state["messages"], config, native=False)]
            reply = await bound_model.ainvoke(messages, config)
        return {"messages": [reply]}
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_graph.py -v`
Expected: all pass, including the streaming test.

- [ ] **Step 4: Commit**

```bash
git add src/eve/graph.py tests/test_graph.py
git commit -m "feat(graph): VOICE sees member photos, falling back to captions on refusal (EVE-21)"
```

---

### Task 21: Checkpoint regression

The design invariant is that no pixel ever reaches Aegra's checkpoints. This task tests it end to end with a real checkpointer.

**Files:**
- Test: `tests/test_images_checkpoint.py`

- [ ] **Step 1: Write the test.** It should pass immediately; it is a regression guard, so confirm it *can* fail by temporarily returning `hydrated` from the `eve` node into state, then revert.

```python
"""tests/test_images_checkpoint.py - the reason image references exist.

A turn with one photo must checkpoint no image bytes and grow the checkpoint
by less than 1 KB over the same turn without the photo (spec 6)."""
from __future__ import annotations

import base64

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from eve.family import Family
from eve.graph import build_graph
from tests.conftest import FakeToolCallingModel
from tests.test_graph import CONFIG, NOAH, _no_extract, _no_recall, _no_suggest
from tests.test_images_hydrate import _row

IMAGE_ID = "aaaaaaaa-0000-4000-8000-000000000001"


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
```

Use a realistic image, not `b"JPEG-a"`, so the byte search is meaningful. Build `row` with `normalise()` on a 400x300 PIL image, as in Task 1, instead of `_row`'s stub bytes.

Run: `uv run pytest tests/test_images_checkpoint.py -v`
Expected: PASS. Then do the sabotage check described above: expect FAIL, and revert.

- [ ] **Step 2: Commit**

```bash
git add tests/test_images_checkpoint.py
git commit -m "test(images): checkpoints hold references, never pixels (EVE-21)"
```

---

### Task 22: `accepts_images` on specialists; the stylist opts in

**Files:**
- Modify: `src/eve/specialists/base.py`, `src/eve/specialists/stylist.py`, `prompts/stylist.md`
- Test: `tests/test_specialists_base.py`, `tests/test_specialists_stylist.py`

**Interfaces:**
```python
def build_specialist(name, tools, system_prompt, permission, model_factory=get_model,
                     *, accepts_images: bool = False) -> BaseTool
# accepts_images=True -> ask(request: str, state, config, image_ids: list[str] | None = None)
```

Behaviour:
- `False` leaves the tool schema **byte-identical** to today's. The snapshot test below pins it.
- `True`: each id in `image_ids` goes through `store.resolve(id, member_sub, thread_id)`. Resolved rows become reference blocks on the inner `HumanMessage`, whose content becomes `[{"type":"text","text":request}, *refs]`. Unresolved ids are dropped, and `span.set_attribute("eve.specialist.images_dropped", n)` records the count.
- The inner `create_agent` gets `middleware=[_image_middleware]` only when `accepts_images`. That middleware is an `@wrap_model_call` async function that does `request.override(messages=await hydrate(request.messages, member_sub_from(request.runtime or config), native=TIER_VISION[Tier.MECHANICAL]))`, then calls the handler. The member sub is read from the inner config's `configurable.member`, which `ask` already sets. Capture it in a closure per call by building the middleware inside `ask`. The agent is cached, so instead read it from `langgraph.config.get_config()` inside the middleware; that returns the inner run's config.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_specialists_base.py`)

```python
import json

SNAPSHOT = {
    "description": "Ask the widgets specialist to handle a request in its domain.",
    "properties": {"request": {"title": "Request", "type": "string"}},
    "required": ["request"],
    "title": "ask_widgets",
    "type": "object",
}


def test_the_default_schema_is_unchanged():
    specialist = build_specialist(
        name="widgets", tools=[get_widget], system_prompt="x",
        permission="home.control", model_factory=lambda _t: None,
    )
    assert json.loads(json.dumps(specialist.tool_call_schema.model_json_schema())) == SNAPSHOT


def test_accepts_images_adds_an_optional_image_ids_list():
    specialist = build_specialist(
        name="widgets", tools=[get_widget], system_prompt="x",
        permission="home.control", model_factory=lambda _t: None, accepts_images=True,
    )
    schema = specialist.tool_call_schema.model_json_schema()
    assert "image_ids" in schema["properties"]
    assert schema["required"] == ["request"]


async def test_image_ids_become_references_on_the_inner_request(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from eve.images.store import ImageRow

    full = "aaaaaaaa-0000-4000-8000-000000000001"
    captured = {}

    class _Agent:
        async def ainvoke(self, payload, config):
            captured["payload"] = payload
            captured["config"] = config
            return {"messages": [AIMessage(content="done")]}

    def fake_create_agent(model, tools, system_prompt, middleware=()):
        captured["middleware"] = middleware
        return _Agent()

    async def fake_resolve(ref, member_sub, thread_id, *, now=None):
        now = datetime.now(UTC)
        if ref == "aaaaaaaa":
            return ImageRow(full, member_sub, thread_id, "upload", None, "image/jpeg",
                            b"", 1, 1, None, now, now + timedelta(days=1))
        return None

    monkeypatch.setattr("eve.specialists.base.create_agent", fake_create_agent)
    monkeypatch.setattr("eve.specialists.base.image_store.resolve", fake_resolve)
    specialist = build_specialist(
        name="widgets", tools=[get_widget], system_prompt="x",
        permission="home.control", model_factory=lambda _t: None, accepts_images=True,
    )
    config = {"configurable": {"thread_id": "t1"}}
    await specialist.coroutine("does this match?", STATE, config, image_ids=["aaaaaaaa", "zzzzzzzz"])

    content = captured["payload"]["messages"][0].content
    assert content == [
        {"type": "text", "text": "does this match?"},
        {"type": "eve_image", "image_id": full, "alt": "image aaaaaaaa"},
    ]
    assert len(captured["middleware"]) == 1


async def test_the_default_specialist_gets_no_middleware(monkeypatch):
    captured = {}

    def fake_create_agent(model, tools, system_prompt, middleware=()):
        captured["middleware"] = middleware
        return _AGENT_STUB

    monkeypatch.setattr("eve.specialists.base.create_agent", fake_create_agent)
    specialist = build_specialist(
        name="widgets", tools=[get_widget], system_prompt="x",
        permission="home.control", model_factory=lambda _t: None,
    )
    await specialist.coroutine("hi", STATE, CONFIG)
    assert tuple(captured["middleware"]) == ()


async def test_the_image_middleware_hydrates_the_inner_model_call(monkeypatch):
    from eve.specialists import base

    seen = {}

    async def fake_hydrate(messages, member_sub, *, native, window=None):
        seen.update(member_sub=member_sub, native=native)
        return ["hydrated"]

    class _Request:
        messages = ["raw"]

        def override(self, **kw):
            seen["override"] = kw
            return self

    async def handler(request):
        return "response"

    monkeypatch.setattr(base, "hydrate", fake_hydrate)
    monkeypatch.setattr(base, "get_config", lambda: {"configurable": {"member": {"sub": "sub-noah"}}})
    result = await base._image_middleware.awrap_model_call(_Request(), handler)

    assert result == "response"
    assert seen["override"] == {"messages": ["hydrated"]}
    assert seen["member_sub"] == "sub-noah"
```

Existing tests fake `create_agent(model, tools, system_prompt)` with three parameters. Always passing `middleware=` would break them, so pass it **only** when `accepts_images` is set, and those fakes keep working.

Run: `uv run pytest tests/test_specialists_base.py -v`
Expected: the new tests FAIL.

- [ ] **Step 2: Implement** in `src/eve/specialists/base.py`

Imports: `from langchain.agents.middleware import wrap_model_call`, `from langgraph.config import get_config`, `from eve.context import principal_sub`, `from eve.images import store as image_store`, `from eve.images.hydrate import hydrate, reference`, and `TIER_VISION` from models.

```python
@wrap_model_call
async def _image_middleware(request, handler):
    """Hydrates reference blocks right before each inner model call, on the
    specialist's own tier (MECHANICAL) - the same function Eve's loop uses,
    so an image means the same thing at both levels (EVE-21, spec 3.3)."""
    member_sub = principal_sub(get_config())
    messages = await hydrate(
        request.messages, member_sub, native=TIER_VISION[Tier.MECHANICAL]
    )
    return await handler(request.override(messages=messages))
```

In `build_specialist`, add the keyword-only `accepts_images: bool = False`, and move the body of `ask` into `async def _run(request, state, config, image_ids) -> str`. Then define exactly one of two signatures, because the schema is inferred from the signature:

```python
    if accepts_images:
        async def ask(
            request: str,
            state: Annotated[EveState, InjectedState],
            config: RunnableConfig,
            image_ids: list[str] | None = None,
        ) -> str:
            return await _run(request, state, config, image_ids or [])
    else:
        async def ask(
            request: str,
            state: Annotated[EveState, InjectedState],
            config: RunnableConfig,
        ) -> str:
            return await _run(request, state, config, [])
```

Inside `_run`:
- When creating the agent, pass `**({"middleware": [_image_middleware]} if accepts_images else {})` to `create_agent`.
- Build the inner human message:

```python
        human: HumanMessage = HumanMessage(request)
        if image_ids:
            thread_id = (config.get("configurable") or {}).get("thread_id")
            refs = []
            for ref in image_ids:
                row = await image_store.resolve(ref, member["sub"], thread_id)
                if row is not None:
                    refs.append(reference(row.id, f"image {image_store.short_id(row.id)}"))
            if len(refs) < len(image_ids):
                span.set_attribute("eve.specialist.images_dropped", len(image_ids) - len(refs))
            if refs:
                human = HumanMessage(content=[{"type": "text", "text": request}, *refs])
```

Then use `{"messages": [human]}` in the `agent.ainvoke` call. The docstring for the `accepts_images` variant should add: `image_ids: ids of photos from this conversation, as written in "[image <id>]".`

`tool(ask)` must still produce `ask_<name>`, so set `__name__`/`__doc__` after the `if`.

- [ ] **Step 3: Opt the stylist in.** In `ask_stylist = build_specialist(...)` add `accepts_images=True`. In `prompts/stylist.md`, add:

```markdown
When the member has sent you photos, you can see them. Judge what they show
against the catalogue; if they ask whether something they photographed goes
with what they own, answer from both.
```

Append to `tests/test_specialists_stylist.py`:

```python
def test_the_stylist_accepts_images():
    schema = stylist_module.ask_stylist.tool_call_schema.model_json_schema()
    assert "image_ids" in schema["properties"]
```

And in `prompts/eve.md`, next to the image bullet from Task 11:

```markdown
- When the member sends a photo it appears as `[image 3f2a9c01]`. To have the
  stylist look at it, pass that id in `image_ids`.
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_specialists_base.py tests/test_specialists_stylist.py tests/test_specialists_*.py tests/test_graph.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/eve/specialists prompts tests/test_specialists_*.py
git commit -m "feat(specialists): opt-in image input; the stylist sees member photos (EVE-21)"
```

**Server half of phase 3 done.** Run `uv run pytest -q`: green except the `acp` baseline. Before the app ships, an old app never sends reference blocks, so this is inert.

---
### Task 23: Flutter: upload photos, then send a content list

**Files:**
- Modify: `lib/data/services/agent/langgraph_agent_service.dart`, `lib/domain/models/sessions/timeline_item.dart`, `lib/ui/features/chat/views/chat_composer.dart`
- Test: `test/data/services/agent/langgraph_agent_service_test.dart`, `test/domain/models/sessions/timeline_item_test.dart`

**Behaviour (§3.1):**
- The images on the last `UserMessage` are `attachmentPath` when `!attachmentIsVideo`, plus `mediaAttachments` whose extension is `jpg|jpeg|png|webp|heic|heif|gif` (case-insensitive). Videos are skipped, so they are refused client-side.
- The thread comes first, so `thread_id` can ride with each upload. Then every image goes through `POST /images` in order, then the run.
- On **any** upload failure, `emit(AgentError('Couldn’t send your photo: ...'))` and return without a run. A turn is never sent with a silently missing image.
- With images, the run's human `content` becomes `[{"type":"text","text": text}, {"type":"eve_image","image_id": id,"alt": "photo sent by <displayName or 'the member'>"}, ...]`. The text block is omitted when the text is empty. Without images, `content` stays the plain string exactly as today.
- Uploaded ids are written to `UserMessage.imageIds`, so the bubble can render them (Task 24), and a retry does not re-upload: images with a recorded id are skipped.
- An action run (`action != null`) never uploads.

**Picker:** HEIC is not decodable by Pillow without an extra plugin. Asking `image_picker` for a quality or size makes iOS re-encode to JPEG, so in `chat_composer.dart` pass `imageQuality: 90, maxWidth: 2048, maxHeight: 2048` to both `pickImage(source: ImageSource.camera, ...)` and `pickMultipleMedia(...)`. The server's 415 remains the backstop.

- [ ] **Step 1: Write the failing tests**

`timeline_item_test.dart`:
```dart
    test('imageIds starts empty and is writable', () {
      final m = UserMessage.create('hi', mediaAttachments: const ['/tmp/a.jpg']);
      expect(m.imageIds, isEmpty);
      m.imageIds.add('x');
      expect(m.imageIds, ['x']);
    });
```

`langgraph_agent_service_test.dart`: extend `FakeLangGraphClient` first:
```dart
  /// Multipart uploads, in order: (path, filePath, fields).
  final List<(String, String, Map<String, String>)> uploads = [];
  final List<Object?> uploadResponses = [];
  Object? uploadError;

  @override
  Future<Object?> postMultipart(String path, {required String field,
      required String filePath, Map<String, String> fields = const {}}) async {
    uploads.add((path, filePath, fields));
    final error = uploadError;
    if (error != null) throw error;
    return uploadResponses.removeAt(0);
  }
```

Then add a group:
```dart
  group('LangGraphAgentService.run: photos', () {
    UserMessage photoMessage(String text, List<String> paths) =>
        UserMessage(text: text, mediaAttachments: paths);

    test('uploads each photo with the thread id, then sends a content list', () async {
      final client = FakeLangGraphClient()
        ..jsonResponses['/threads'] = {'thread_id': 't1'}
        ..uploadResponses.addAll([
          {'image_id': 'id-1', 'width': 1, 'height': 1},
          {'image_id': 'id-2', 'width': 1, 'height': 1},
        ])
        ..frames = [endFrame];
      final message = photoMessage('does this go?', ['/p/a.jpg', '/p/b.PNG', '/p/c.mov']);
      final session = Session(id: 's', items: [message]);

      await LangGraphAgentService(client: client, displayName: 'Noah').run(session);

      expect(client.uploads.map((u) => u.$2), ['/p/a.jpg', '/p/b.PNG']);
      expect(client.uploads.first.$3, {'thread_id': 't1'});
      final input = client.runs.single.value['input'] as Map;
      expect((input['messages'] as List).single, {
        'role': 'user',
        'content': [
          {'type': 'text', 'text': 'does this go?'},
          {'type': 'eve_image', 'image_id': 'id-1', 'alt': 'photo sent by Noah'},
          {'type': 'eve_image', 'image_id': 'id-2', 'alt': 'photo sent by Noah'},
        ],
      });
      expect(message.imageIds, ['id-1', 'id-2']);
    });

    test('a photo with no text sends only the image block', () async {
      final client = FakeLangGraphClient()
        ..uploadResponses.add({'image_id': 'id-1', 'width': 1, 'height': 1})
        ..frames = [endFrame];
      final session = Session(id: 's', items: [photoMessage('', ['/p/a.jpg'])])
        ..remoteKey = 't1';

      await LangGraphAgentService(client: client).run(session);

      final content = ((client.runs.single.value['input'] as Map)['messages'] as List)
          .single['content'] as List;
      expect(content, [
        {'type': 'eve_image', 'image_id': 'id-1', 'alt': 'photo sent by the member'},
      ]);
    });

    test('an upload failure blocks the send and says so', () async {
      final client = FakeLangGraphClient()
        ..uploadError = LangGraphException(415, 'not an image')
        ..frames = [endFrame];
      final service = LangGraphAgentService(client: client);
      final events = <AgentEvent>[];
      final sub = service.events.listen(events.add);
      final session = Session(id: 's', items: [photoMessage('hi', ['/p/a.jpg'])])
        ..remoteKey = 't1';

      await service.run(session);
      await Future<void>.delayed(Duration.zero);
      await sub.cancel();

      expect(client.runs, isEmpty);
      expect(events.whereType<AgentError>().single.message, contains('photo'));
    });

    test('text-only messages keep the plain string', () async {
      final client = FakeLangGraphClient()..frames = [endFrame];
      await LangGraphAgentService(client: client)
          .run(sessionWith('hello')..remoteKey = 't1');
      expect(client.uploads, isEmpty);
      expect(((client.runs.single.value['input'] as Map)['messages'] as List).single,
          {'role': 'user', 'content': 'hello'});
    });

    test('a retry does not re-upload photos that already have ids', () async {
      final client = FakeLangGraphClient()..frames = [endFrame];
      final message = photoMessage('again', ['/p/a.jpg'])..imageIds.add('id-1');
      await LangGraphAgentService(client: client)
          .run(Session(id: 's', items: [message])..remoteKey = 't1');
      expect(client.uploads, isEmpty);
    });
  });
```

Check `Session`'s real constructor (`grep -n "class Session" -A15 lib/domain/models/sessions/session.dart`) and match it. `sessionWith` in the test file shows the working form.

Run: `flutter test test/data/services/agent/langgraph_agent_service_test.dart test/domain/models/sessions/timeline_item_test.dart`
Expected: the new tests FAIL.

- [ ] **Step 2: Implement**

`timeline_item.dart`: add this to `UserMessage`:
```dart
  /// Eve image ids for this message's photos: set after upload by
  /// `LangGraphAgentService`, or parsed from history. Drives the bubble's
  /// thumbnails and stops a retry from uploading the same photo twice.
  final List<String> imageIds;
```
Also add the constructor param `List<String>? imageIds` with `: imageIds = imageIds ?? []`. It must be a growable list, not `const []`. Thread it through `create`.

`langgraph_agent_service.dart`:
```dart
  static final _imageExtension = RegExp(r'\.(jpe?g|png|webp|heic|heif|gif)$', caseSensitive: false);

  static List<String> _photoPaths(UserMessage message) => [
    if (message.attachmentPath != null && !message.attachmentIsVideo) message.attachmentPath!,
    ...message.mediaAttachments.where(_imageExtension.hasMatch),
  ];

  /// Uploads [message]'s photos that have no id yet. Throws on the first
  /// failure: the caller refuses to send a turn with a missing image
  /// (eve-ai EVE-21, spec 3.1).
  Future<void> _uploadPhotos(UserMessage message, String threadId) async {
    final paths = _photoPaths(message);
    for (final path in paths.skip(message.imageIds.length)) {
      final response = await _client.postMultipart(
        '/images', field: 'file', filePath: path, fields: {'thread_id': threadId});
      final id = response is Map ? response['image_id']?.toString() : null;
      if (id == null || id.isEmpty) throw StateError('POST /images returned no image_id');
      message.imageIds.add(id);
    }
  }

  Object _humanContent(String text, UserMessage? message) {
    final ids = message?.imageIds ?? const <String>[];
    if (ids.isEmpty) return text;
    final who = _displayName.isEmpty ? 'the member' : _displayName;
    return [
      if (text.isNotEmpty) {'type': 'text', 'text': text},
      for (final id in ids) {'type': 'eve_image', 'image_id': id, 'alt': 'photo sent by $who'},
    ];
  }
```
In `run`, after `final threadId = await _threadIdFor(session);`:
```dart
      final lastUser = userMessages.isEmpty ? null : userMessages.last;
      if (action == null && lastUser != null && _photoPaths(lastUser).isNotEmpty) {
        try {
          await _uploadPhotos(lastUser, threadId);
        } catch (error) {
          final message = _redactUris(error.toString());
          log.warning('LangGraphAgentService: photo upload failed: $message');
          emit(AgentError('Couldn’t send your photo: $message'));
          return;
        }
      }
```
In the run body, use `'content': action == null ? _humanContent(inputText, lastUser) : inputText`.

`_displayName` is the member's name as the service was configured. Confirm by reading how `displayName` is used elsewhere in the file. If it is the *assistant's* display name, drop it and always use `'photo sent by the member'`.

`chat_composer.dart`: add `imageQuality: 90, maxWidth: 2048, maxHeight: 2048` to the `pickImage(source: ImageSource.camera)` call and to `pickMultipleMedia()`.

- [ ] **Step 3: Run the tests**

Run: `flutter analyze && flutter test test/data/services/agent test/domain test/ui/features/chat`
Expected: clean, and all pass.

- [ ] **Step 4: Commit**

```bash
git add lib test
git commit -m "feat(agent): upload photos to Eve and send them as image references (EVE-21)"
```

---

### Task 24: Flutter: photo thumbnails in user bubbles, live and after reopening

**Files:**
- Modify: `lib/data/services/agent/langgraph_agent_service.dart` (`loadHistory`, `_splitContent` callers), `lib/ui/features/chat/views/chat_message.dart`
- Test: `test/data/services/agent/langgraph_agent_service_test.dart`, `test/ui/features/chat/views/chat_message_test.dart` (existing; check with `ls test/ui/features/chat/views`)

- [ ] **Step 1: Write the failing tests**

In the `loadHistory` group:
```dart
    test('a human message with photos comes back with its image ids', () async {
      final client = FakeLangGraphClient()
        ..jsonResponses['/threads/t1/state'] = {
          'values': {
            'messages': [
              {
                'type': 'human',
                'content': [
                  {'type': 'text', 'text': 'does this go?'},
                  {'type': 'eve_image', 'image_id': 'id-1', 'alt': 'photo sent by Noah'},
                ],
              },
            ],
          },
        };
      final items = await LangGraphAgentService(client: client).loadHistory('t1');
      final message = items.single as UserMessage;
      expect(message.text, 'does this go?');
      expect(message.imageIds, ['id-1']);
    });

    test('a photo-only human message is not dropped', () async {
      final client = FakeLangGraphClient()
        ..jsonResponses['/threads/t1/state'] = {
          'values': {
            'messages': [
              {'type': 'human', 'content': [
                {'type': 'eve_image', 'image_id': 'id-1', 'alt': 'photo'},
              ]},
            ],
          },
        };
      final items = await LangGraphAgentService(client: client).loadHistory('t1');
      expect((items.single as UserMessage).imageIds, ['id-1']);
    });
```

In `chat_message_test.dart`, mirror an existing user-bubble test and add:
```dart
  testWidgets('a user message with image ids renders a thumbnail per id', (tester) async {
    // Build the same way the file's existing user-message tests do, with
    // UserMessage(text: 'look', imageIds: ['id-1', 'id-2']).
    // Expect two EveImage widgets, in a Wrap, above the text.
  });
```
Fill in the harness from that file's existing pattern. Pump through `pumpApp` with the default `ConversationViewModel`, whose `imageLoader` is null, so the tiles render the unavailable state. That is fine: the test asserts `find.byType(EveImage)` finds exactly 2.

Run: `flutter test test/data/services/agent/langgraph_agent_service_test.dart test/ui/features/chat/views`
Expected: the new tests FAIL.

- [ ] **Step 2: Implement**

`loadHistory` `case 'human':`
```dart
          final content = message['content'];
          final text = _splitContent(content).text;
          final imageIds = [
            if (content is List)
              for (final block in content)
                if (block is Map && block['type'] == 'eve_image' && block['image_id'] is String)
                  block['image_id'] as String,
          ];
          if (text.isNotEmpty || imageIds.isNotEmpty) {
            items.add(UserMessage(text: text, imageIds: imageIds));
          }
```

`chat_message.dart`: in `build`, where `messageContent` is chosen for a non-agent item, wrap user messages that have ids:
```dart
        Widget messageContent = /* existing expression */;
        if (item is UserMessage && item.imageIds.isNotEmpty) {
          messageContent = Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Wrap(
                spacing: AppSpacing.sm,
                runSpacing: AppSpacing.sm,
                children: [
                  for (final id in item.imageIds)
                    SizedBox(
                      width: 160,
                      child: EveImage(
                        imageId: id,
                        alt: 'Photo you sent',
                        loader: provider.imageLoader,
                      ),
                    ),
                ],
              ),
              if (content.isNotEmpty) ...[
                const SizedBox(height: AppSpacing.sm),
                messageContent,
              ],
            ],
          );
        }
```
`messageContent` is currently `final`, so make it non-final. Add the `EveImage` import.

- [ ] **Step 3: Run the tests**

Run: `flutter analyze && flutter test`
Expected: the whole suite is clean.

- [ ] **Step 4: Commit**

```bash
git add lib test
git commit -m "feat(chat): show sent photos as thumbnails, including after reopening (EVE-21)"
```

---

### Task 25: Final verification

- [ ] **Step 1: Server, all tiers**

```bash
cd /Users/nchalifo/GitHub/eve-ai/.worktrees/eve-21-images
uv run pytest -q
docker compose -f docker-compose.test.yml up -d && uv run pytest -m integration -q
```
Expected: unit is green except the 4 known `acp` collection errors. Integration is green, and it covers `test_images_store.py`.

- [ ] **Step 2: Flutter**

```bash
cd ~/GitHub/open-assistant/flutter-open-assistant && flutter analyze && flutter test
```
Expected: clean.

- [ ] **Step 3: Manual end-to-end against a local server.** Only start a server here if the user agrees; otherwise, list these checks for them.
  1. Ask "what should I wear today?". The stylist's answer cites `[image …]`, and a card shows the garment photo.
  2. Send a photo with "does this go with my navy blazer?". Eve and the stylist answer about the photo, and the bubble shows the thumbnail.
  3. Reopen the thread. The thumbnail and the stylist card both re-render.
  4. Run `psql ... -c "select id, origin, octet_length(bytes), expires_at from eve_image"`. Rows exist with sizes under 1 MB, `upload` rows expire in 30 days, and `immich` rows expire in 1 day.
  5. Run `uv run eve-images sweep`. It reports a count.

- [ ] **Step 4: Linear.** Comment on EVE-21 with what shipped and the recorded `TIER_VISION` outcome, and link the two PRs plus the infra PR.

- [ ] **Step 5: Hand off** with superpowers:finishing-a-development-branch in each repo.

---

## Self-review notes (author)

- **Spec coverage:**

  | Spec section | Task(s) |
  |---|---|
  | §2.1 | 1, 4 |
  | §2.2 | 19 (`reference`) |
  | §2.3 | 5, 6 |
  | §2.4 | 3, 5 (`put` expiry), 6 (410), 7, 8 |
  | §3.1 | 23 |
  | §3.2 | 2, 18, 19, 20 |
  | §3.3 | 22 |
  | §3.4 | 13, 14 |
  | §4.1 | 10, 11, 12 |
  | §4.2 | 10, 12, 15, 17 |
  | §4.3 | 12 (persist bound), 16 (410 tile) |
  | §4.4 | 15, 16, 17, 23, 24 |
  | §5 | 6, 12, 13, 19, 20, 23 |
  | §6 | tests in each task, plus 21 |
  | §7 | the phase order above |

- **Deliberate deviations from the spec, all small:**
  - `store.put` takes a `Normalised`, not raw bytes, so decoding stays off the DB connection.
  - `store.resolve` returns the row, not just the id.
  - `assistant_ui.catalogVersion` stays `"1"` and versions travel in `catalog_versions`, so an up-to-date phone keeps working against a server that predates this change.
  - The stylist fetches photos by garment name (`photo_of`) rather than by embedding asset ids in the catalogue, so the per-call prompt stays the same size.
- **Blocking unknown:** the Task 2 probe outcome. Both outcomes are fully handled; the probe only picks which path is the default.
