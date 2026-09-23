# Images through Eve: design (EVE-21)

Status: approved in brainstorming, 2026-09-22.
Issue: [EVE-21](https://linear.app/chalifour-development/issue/EVE-21/images-through-eve-vision-into-specialists-and-images-out-to-the-ui)
Repos touched: `eve-ai` (server), `flutter-open-assistant` (client). Both ship
together for phases 2 and 3.

## 1. Problem

Eve is text-only in both directions.

- **In:** a specialist is a string-in, string-out tool (`build_specialist`,
  `src/eve/specialists/base.py`). The Flutter composer already captures photos
  (`UserMessage.attachmentPath`, `mediaAttachments`), but
  `langgraph_agent_service.dart` sends only `lastUserText`, so attachments are
  silently discarded today.
- **Out:** `assistant-ui/1.0` has a closed component catalogue
  (`src/eve/ui/protocol.py`) with no image type, and the client rejects unknown
  types silently.

Both halves share three questions answered once here: where bytes live, how
long they live, and what an image *is* in this system.

Not in scope: video, audio, image generation, images inside widget snapshots.

## 2. What an image is

An image is a row in Eve's Postgres, addressed by an opaque UUID. Nothing else
in the system carries pixels: not state, not checkpoints, not tool arguments,
not UI frames.

### 2.1 Table `eve_image` (Alembic migration 0014)

| column | type | notes |
|---|---|---|
| `id` | uuid pk | the image id everywhere else |
| `member_sub` | text | owner; every read checks it |
| `thread_id` | text null | thread it arrived in or was minted for |
| `origin` | text | `upload` or `immich` |
| `source_ref` | text null | Immich asset id for `origin=immich`; unique with `member_sub` for dedupe |
| `content_type` | text | `image/jpeg` or `image/webp` after re-encode |
| `bytes` | bytea | downscaled image |
| `width`, `height` | int | after downscale; drives `aspect` |
| `caption` | text null | REFLEX caption, filled only on the caption path (section 3.2) |
| `created_at` | timestamptz | |
| `expires_at` | timestamptz | indexed; retention (section 2.4) |

On write, the image is decoded, EXIF-orientation corrected, EXIF stripped,
downscaled so the longest side is at most 1568 px, and re-encoded (JPEG,
quality 85) with a 1 MB ceiling. One size serves both the model and the phone.
Pillow is added as a dependency.

### 2.2 The reference block

The only shape that enters `EveState` and therefore Aegra's checkpoints:

```json
{"type": "eve_image", "image_id": "3f2a…", "alt": "photo sent by Noah"}
```

It sits inside a message's content list next to text blocks. It is a few dozen
bytes, so a thread's checkpoint growth per image is bounded and independent of
image size. This is the `DynamicToolSpec` reasoning (Phase 3 design, 5.1)
applied to pixels.

### 2.3 Module `eve.images` and its routes

`eve.images.store`: `put(member_sub, raw, *, origin, thread_id, source_ref)
-> ImageRow`, `get(image_id, member_sub) -> ImageRow | None` (returns `None` for
missing, foreign, or expired), `sweep(now) -> int`.

`eve.images.app`: an `APIRouter(dependencies=[Depends(require_auth)])`
included by `src/eve/http_app.py`, same pattern as widgets and routines.

- `POST /images`: multipart, one file. The member comes from the principal.
  Returns `{"image_id": ..., "width": ..., "height": ...}`. 415 for a file that
  is not a decodable image, 413 above 15 MB before downscale.
- `GET /images/{id}`: returns the bytes with the stored `content_type` and
  `Cache-Control: private, max-age=3600`. **Owner-only**: a different member
  gets 404, never 403, so ids do not leak existence. Expired rows return 410.

Upload happens before the run, out of band, because LangGraph writes run input
into checkpoint writes before any node could strip it. Base64 in the run input
would defeat the whole reference design.

### 2.4 Retention

- `origin=upload`: `expires_at = created_at + image_retention_days`, a new
  `Settings` field defaulting to 30.
- `origin=immich`: a cache copy with `expires_at = created_at + 1 day`. Immich
  is the source of truth, and a later `from_immich` for the same asset refreshes
  the row.
- A nightly sweep deletes expired rows. It runs as a CLI entry point
  (`eve-images sweep`) triggered by a Kubernetes CronJob in the infrastructure
  repo, using the eve image and its existing database secret.
- Expiry is also enforced at read time, so a missed sweep never serves an
  expired image.
- A reference to an expired or deleted image is not an error anywhere: it
  hydrates to text and renders as a placeholder tile.

A member photo therefore lives 30 days by explicit decision, not forever by
default. The checkpoint keeps only the reference, and the reference outlives
the bytes harmlessly.

## 3. Images into the graph

### 3.1 Phone to Eve

When a user message has image attachments, `langgraph_agent_service.dart`
uploads each via `POST /images`, then sends the human message as a content
list:

```json
[{"type": "text", "text": "does this go with my navy blazer?"},
 {"type": "eve_image", "image_id": "3f2a…", "alt": "photo sent by Noah"}]
```

A text-only message keeps today's plain string, so nothing changes on the
common path. If any upload fails, the attachment shows an inline error and the
turn is not sent; a turn is never sent with a silently missing image. Video
attachments are refused client-side.

### 3.2 Hydration at the model boundary

`eve.images.hydrate(messages, member_sub, *, window=2) -> list` is the one
place references become pixels. `graph.py::_stripped_for_model` already
rewrites messages before every VOICE call; `hydrate` runs there.

- It returns a new list and never mutates state. Hydrated bytes exist only for
  the one model call.
- Only references in the last `window` human turns (setting
  `image_hydrate_turns`, default 2) are hydrated. Older references become
  `[image <short-id>: <alt>]` text. This keeps a long thread from re-sending
  every photo on every turn.
- Each hydrated image is preceded by a text label `[image <short-id>]` so the
  model can name ids it wants to pass to a specialist or a surface. The short
  id is the first 8 hex characters. `store.resolve(short_or_full, member_sub,
  thread_id)` turns it back into a full id by prefix match over that member's
  rows in that thread; zero or several matches is "unresolvable". Every
  consumer (specialist `image_ids`, surface `imageId`) resolves through this
  one function, so ids cited in earlier turns or by specialists work the same
  way.
- A missing, foreign, or expired reference becomes
  `[image no longer available]`. The turn never fails because of an image.

Hydrated form, native path: a LangChain standard image block
`{"type": "image", "base64": ..., "mime_type": ...}`.

**Native versus caption is decided by a live probe, not assumed.** The first
plan task adds `tests/test_live_models.py` cases that send an image to
`chatgpt/gpt-5.6-terra` and `chatgpt/gpt-5.6-luna` through LiteLLM's
Responses mode, and through the Anthropic fallback hop. The result is recorded
in `models.py` as `TIER_VISION: dict[Tier, bool]`, keeping `models.py` the only
file that knows model capabilities.

- Tier marked `True`: hydrate to native image blocks.
- Tier marked `False`: hydrate to text `[image <short-id>: <caption>]`, where
  the caption is a REFLEX (Gemini) description generated once and cached in
  `eve_image.caption`. With this fallback the design still works end to end,
  just with less visual detail.

`REFLEX` is Gemini and is already known to be multimodal
(`src/eve/wardrobe/vision.py` uses it). No new tier is added.

### 3.3 Specialists, opt-in

`build_specialist(..., accepts_images: bool = False)`.

- `False` (default): the tool schema is byte-for-byte today's. Home, Mail,
  Finances, and Health are untouched.
- `True`: `ask(request: str, image_ids: list[str] = [], ...)`. Ids may be full
  or short form. The inner `HumanMessage` becomes a content list of the request
  text plus reference blocks, and the inner `create_agent` loop hydrates them
  through the same `hydrate` function via a model-call middleware, against the
  inner loop's tier (`MECHANICAL`) and its `TIER_VISION` entry. Ids that do not
  resolve for this member are dropped with a span attribute, not an error.

The stylist is the first adopter.

### 3.4 Specialists returning images

A specialist still returns `str`. It cannot return bytes, and it does not need
to.

`eve.images.from_immich(member_sub, asset_id, *, thread_id) -> str | None`
fetches the preview through eve-tools' existing `immich.asset_image`, stores
(or refreshes) an `origin=immich` row deduplicated on
`(member_sub, source_ref)`, and returns the image id. It returns `None` if
Immich is unreachable, so no broken id is ever minted. The stylist gets a tool
wrapping it and is prompted to cite ids as `[image <short-id>]` in its answer.
Eve reads those ids and may place them in a surface (section 4).

Immich's API key stays in eve-tools. The phone never learns an Immich URL.

## 4. Images out to the UI

### 4.1 The `image` component

`protocol.py` adds `image` to `CATALOG_IDS` and `_ALLOWED_PROPERTIES`:

| property | required | rule |
|---|---|---|
| `imageId` | yes | UUID regex |
| `alt` | yes | string, at most `MAX_STRING` |
| `aspect` | no | `square`, `portrait`, or `landscape` |

There is no URL property. The client only fetches `GET /images/{imageId}` on
Eve's own base URL with the bearer token it already sends. The client never
fetches a model-chosen URL, and Eve is the proxy by construction.

`image` is chat-only: `validate_operation(..., widget=True)` rejects it.
Widget snapshots refresh on a schedule and need their own lifetime design.

**Id provenance check.** Before emission, `eve.ui.surface` and `eve.ui.stream`
resolve each `imageId` through `store.resolve` (3.2) and rewrite it to the
full id. An id that does not resolve is replaced by a
`text` component carrying the alt, and a server-side log line is emitted. This
turns the client's silent drop into a server diagnostic, which is the reason
`protocol.py` exists.

`aspect` is filled from the stored `width`/`height` when the model omits it.

### 4.2 Versioning by capability

The client advertises what it renders:
`config.configurable.catalog_versions = ["1", "2"]`.

- A surface containing an `image` component is stamped
  `catalogVersion: "2"`. Every other surface stays `"1"`.
- If the run did not advertise `"2"` (an older client), each `image` component
  is replaced by a `text` component with its alt before validation, and the
  surface stays `"1"`. An older phone gets a readable surface rather than a
  dropped one.
- `protocol.py` gains `CATALOG_VERSIONS = {"1": CATALOG_IDS_V1, "2":
  CATALOG_IDS_V1 | {"image"}}`. Validation checks a surface's components
  against the set for its stamped version.

`CATALOG_VERSION` now means something specific: the component set a surface
needs. The server downgrades to fit the client.

Known and accepted edge: a v2 frame persisted by an updated device, reopened
later on a v1 device, drops that one surface on the v1 device.

### 4.3 Persistence

`persist_ui` stores the frame as emitted. An image costs its id plus its alt
in the transcript, on the order of 100 bytes.

On a reopened session the client fetches the image again. After expiry, `GET`
returns 410 and the client renders an "image no longer available" tile with the
alt text.

### 4.4 Flutter changes

- `dynamic_surface_protocol.dart` and `dynamic_surface.dart`: accept
  `catalogVersion` in `{"1", "2"}`, add `image` to the version-2 set with the
  property rules in 4.1, and keep `image` illegal in widget snapshots.
- An image renderer: authenticated fetch against Eve's base URL, in-memory
  cache keyed by id, placeholder sized by `aspect` while loading, and a
  fallback tile with alt text for 404, 410, or network errors.
- `langgraph_agent_service.dart`: upload attachments, send the content list
  (3.1), and add `catalog_versions` to run config.
- `loadHistory`: parse `eve_image` blocks in user messages so a member's own
  sent photos render as thumbnails in a reopened session.

## 5. Error handling

| failure | behaviour |
|---|---|
| upload not an image / undecodable | 415, inline error on that attachment, turn not sent |
| upload over 15 MB | 413, same |
| reference missing, foreign, expired at hydrate | `[image no longer available]` text; turn proceeds |
| model rejects image input at runtime (e.g. a fallback hop) | retry the call once with caption text instead of pixels, generating missing captions on REFLEX; span attribute `eve.images.caption_fallback` |
| Immich unreachable in `from_immich` | returns `None`; specialist gets the usual degraded `invoke` string; no id minted |
| surface cites an unresolvable `imageId` | component becomes alt text; server log |
| sweep fails | logged; next night retries; read-time expiry still enforced |

## 6. Testing

Unit (fakes for model and Immich):

- store: round trip, downscale bounds, EXIF stripped, dedupe on
  `(member_sub, source_ref)`, sweep deletes only expired rows.
- routes: upload 200/413/415; GET owner 200, other member 404, expired 410.
- hydrate: last-N window, older refs become text, missing/foreign/expired
  become the placeholder, native vs caption by `TIER_VISION`, input list never
  mutated.
- `store.resolve`: full id, unique short id, ambiguous short id, other
  member's id, other thread's id.
- `build_specialist`: `accepts_images=False` schema unchanged (snapshot of the
  tool's JSON schema); `True` adds `image_ids` and hydrates the inner call.
- protocol: `image` property validation, rejected in widgets, v2 stamping, v1
  downgrade to text, provenance replacement.
- `persist_ui`: persisted frame with an image stays under a stated byte bound.
- **Checkpoint regression:** a graph turn with one uploaded image; assert the
  serialised checkpoint contains no base64 image data and grows by less than
  1 KB relative to the same turn without an image.

Live (existing live-test convention):

- the tier probe (3.2) for `chatgpt/gpt-5.6-terra`, `chatgpt/gpt-5.6-luna`, and
  the Anthropic fallback.

Flutter:

- validator parity tests using the same fixtures as the `protocol.py` tests.
- renderer golden tests for loading, loaded, and 410 states.
- service test: attachments upload first, then content-list send; upload
  failure blocks the send.

## 7. Phasing

One spec, one implementation plan, three shippable steps.

1. **Foundation** (server only, no visible change): tier probe and
   `TIER_VISION`, `eve_image` table and store, `/images` routes, retention
   settings, sweep CLI and CronJob.
2. **Images out** (coordinated Flutter release): `image` component, capability
   versioning, provenance check, `from_immich`, stylist tool and prompt, client
   validator and renderer. Payoff: the stylist shows you the blazer.
3. **Images in** (coordinated Flutter release): client upload and content-list
   send, `hydrate` in `_stripped_for_model`, `accepts_images` on the stylist,
   `loadHistory` thumbnails.

Out ships before in: it is the case the issue names, and it exercises the
store and proxy end to end before any sensitive member photo is stored.

## 8. Decisions recorded

- Bytes in Eve's Postgres, not SeaweedFS or a PVC: nothing new to deploy,
  covered by existing CNPG backups, and retention is a `DELETE`. Revisit if the
  table exceeds a few GB.
- Retention 30 days for uploads, 1 day for Immich cache copies, by setting.
- Owner-only reads. Household sharing is a one-line policy change if wanted.
- Native vision on existing tiers, gated by a live probe, with a REFLEX caption
  fallback. No new tier.
- Specialists opt in to images; the default signature does not widen.
- Catalogue versioning by client capability with server-side downgrade.
- No URL property on `image`; Eve proxies everything.
