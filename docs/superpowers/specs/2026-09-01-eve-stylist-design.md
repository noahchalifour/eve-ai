# The stylist — design

**Issue:** [EVE-20 Create personal stylist specialist](https://linear.app/chalifour-development/issue/EVE-20/create-personal-stylist-specialist)
**Status:** Design approved, not yet implemented
**Date:** 2026-09-01

## What this is

Eve gains a stylist. You ask her what to wear; she reads a catalogue of the
clothes you actually own, checks the weather and what's on your calendar, and
tells you what to put on and why.

This is a sixth specialist alongside Home, Mail and Finances, and the first
one whose subject matter is a set of objects rather than a service API. The
clothes live in an Immich album you photograph yourself; the catalogue is
built from that album by a vision pass that runs once per garment.

It also makes one change to shared infrastructure that every specialist
inherits: skills can now be scoped to a single specialist, and Eve does not
see the scoped ones.

## What this is not

EVE-20 as filed describes two capabilities: *what should I wear today*, and
*what should I buy*. They share a wardrobe and almost nothing else. Dressing
is synchronous, answered within the turn from local state. Shopping is
minutes long, drives a real browser through Pinterest, TikTok and store
catalogues, and reports back later through the ambient path. Different
latency, different tools, different failure modes.

**This spec is dressing only.** Shopping gets its own issue and its own
design, and it will be substantially cheaper to write once a wardrobe exists
to find the gaps in — which is the other reason to build this half first.

## Eve cannot show you a photograph

This constraint shapes everything downstream, so it goes first.

The `assistant-ui/1.0` catalogue is closed at thirteen component types
(`src/eve/ui/protocol.py:29`) and none of them is an image. The Flutter
client validates against the same closed set and rejects anything else
*silently* — the surface becomes one neutral "this content can't be shown"
card, or on the `custom` path is dropped with a log line that never leaves
the phone. Widening the catalogue means changing a different repository.

So the stylist's answer is words: "the navy wool blazer over the white
oxford, with the brown chelsea boots." Every garment therefore needs a name
that you and Eve both recognise, and producing those names is a first-class
job of the catalogue rather than a nicety.

It also raises the cost of a hallucinated garment. If Eve could show you a
photograph, recommending a coat you don't own would be self-correcting. In
text it is not: you go to the wardrobe, the coat isn't there, and the whole
feature has lied to you. See "Never name a garment you haven't seen", below.

## How Eve perceives a wardrobe

Three approaches were considered.

**Catalogue once, reason over text** — chosen. A sync walks the Immich album,
runs each new photo through a vision model exactly once, and stores a
structured record per garment. At query time the stylist reads the whole
catalogue as text.

**CLIP search only** — rejected. Immich's smart search
(`POST /api/search/smart`) is genuinely good and is CLIP-backed over pgvector,
and it accepts an `albumIds` filter, so "find me a warm waterproof jacket"
against the wardrobe album works. But outfit reasoning needs to *enumerate*,
not retrieve: "all my trousers", "do I own anything warm enough for -5°C",
"is there a formal option in here at all". A similarity search cannot answer
a question whose answer is *no*. It also returns asset ids, and per the
section above, an asset id is not something Eve can say out loud.

**Vision at query time** — rejected. Most accurate and never stale, but it
breaks the specialist contract: `build_specialist` wraps a loop that takes a
string and returns a string, and pixels cannot travel through that. It would
also pay a vision bill on every single "what should I wear".

Immich's own machine learning does not remove the need for the vision pass.
Its auto-tagging is coarse — "clothing", "shirt" — and its smart search is
the same CLIP embedding by another name. Neither yields fabric, warmth or
formality, which are the three attributes outfit assembly actually turns on.

**The consequence worth stating plainly: pixels never leave the sync path.**
No image enters Eve's graph, a specialist loop, or an Aegra checkpoint. The
catalogue is text from the moment it is written, which is what keeps
`build_specialist` unmodified and keeps checkpointed state small.

## Architecture

Six pieces, one new package.

### `eve-tools` gains an `immich.*` namespace

Two handlers in `src/eve_tools/app.py`'s `_HANDLERS` table:

- `immich.album_assets` — the wardrobe album's assets: ids and whatever
  metadata the catalogue wants to keep alongside them.
- `immich.asset_image` — one asset's preview, base64-encoded.
  `GET /api/assets/{id}/thumbnail?size=preview` returns the large JPEG.

Two new `ToolsSettings` fields, `immich_url` and `immich_api_key`
(`EVE_TOOLS_IMMICH_URL`, `EVE_TOOLS_IMMICH_API_KEY`), and the corresponding
entries in `.env.example`. This is ADR 0006 applied unchanged: Immich is a
third-party credential, so it lives in the one process that holds those, and
Eve's container never sees the key.

**To verify during implementation planning, against the live instance:** the
exact album endpoint and its response shape (`GET /api/albums/{id}` is
believed to return the album with its `assets[]`, but this was not confirmed
from the rendered API documentation), and that the API key travels as
`x-api-key`. The smart-search shape and the thumbnail endpoint were both
confirmed. Note also
[immich#29858](https://github.com/immich-app/immich/issues/29858): smart
search misbehaves when more than one album id is supplied. One album is all
this design uses, so it is not a blocker, but do not build on multi-album
filtering.

### `src/eve/wardrobe/` — new package

- **`store.py`** — every `eve_wardrobe_item` statement, nothing else. Same
  shape and same discipline as `src/eve/computer/store.py`.
- **`catalog.py`** — the sync. List the album, diff against the table, fetch
  a preview for each uncatalogued asset, one vision call per asset, insert
  the resulting rows; delete rows whose assets have left the album.
- **`cli.py`** — `eve-wardrobe sync|list`, joining `eve-eval`, `eve-migrate`,
  `eve-pat`, `eve-skill` and `eve-tool` in `[project.scripts]`.

The vision call lives here, in Eve's main container, because this is where
the model credential is. `eve-tools` holds third-party credentials and runs
no models; giving it one would blur a boundary ADR 0006 draws deliberately.
The cost is that base64 image bytes cross the HTTP hop — a preview is a few
hundred kilobytes encoded, which is unremarkable for a batch job and would
be unacceptable in a conversational turn, which is another reason cataloguing
is not one.

### `src/eve/specialists/stylist.py`

One `build_specialist()` call. Permission `wardrobe`. Four tools of its own:

- `read_wardrobe` — the catalogue, rendered as text, grouped by category.
- `todays_weather` — `home.weather`, the handler `src/eve/ui/tools.py:49`
  already calls with no permission gate.
- `list_events` — `calendar.list_events`, with a `calendar.read` check
  *inside* the tool.
- `sync_wardrobe` — the same function the CLI calls.

Plus the scoped skills search that `build_specialist` now appends to every
specialist (below).

### `prompts/stylist.md`, `skills/dress-for-the-day/SKILL.md`

The system prompt carries operating discipline; the skill carries dressing
heuristics. Both are covered under "Behaviour".

### `family.yaml` and `graph.py`

A `wardrobe` grant for both adults and a `wardrobe_album` field per member
(with the matching optional field on `Member` in `src/eve/family.py`), and
`ask_stylist` added to `_BASE_TOOLS` (`src/eve/graph.py:65`).

## The calendar

`calendar.list_events` has existed in `eve-tools` since Phase 4, but only
`eve_ambient` has ever called it (`src/eve_ambient/sources/calendar.py:80`).
Nothing in the conversational graph can read your day.

The stylist gets a thin tool over that existing handler rather than a new
calendar specialist. The handler exists, the `calendar.read` permission
exists, and the marginal cost is one function — while the difference between
knowing you have a client meeting at two and not knowing is the difference
between a stylist and a thing that reads a thermometer.

A general `ask_calendar` specialist is real value and is not this ticket.
Specialists do not call each other, so the stylist would need its own
calendar tool even if one existed.

Permission is checked twice, which is `mail.py`'s established pattern: the
coarse `wardrobe` grant at the Eve → stylist edge, and the fine `calendar.read`
grant inside `list_events`, exactly as `send_email` requires `mail.send`
inside a specialist a `mail.read`-only member can still reach.

## The garment record

One table, `eve_wardrobe_item`, by Alembic migration.

| column | type | why |
|---|---|---|
| `id` | uuid pk | |
| `member_sub` | text | Scoping, as in every other table here. |
| `asset_id` | text | The Immich asset. Unique with `member_sub`, `item_index`. |
| `item_index` | int | One photo is not always one garment — see below. |
| `name` | text | `"navy wool blazer"`. The label Eve says out loud. |
| `category` | text | `top`/`bottom`/`outerwear`/`footwear`/`accessory`/`full` |
| `attrs` | jsonb | colour, pattern, fabric, warmth 1–5, formality 1–5, season, fit notes |
| `catalogued_at` | timestamptz | |

**Why `attrs` is jsonb.** Nothing queries on fabric. `read_wardrobe` reads
the whole table and renders it as text, so the only consumer is a prompt. The
vision prompt's vocabulary will drift as it is tuned, and a migration per
drift buys nothing. `name` and `category` stay real columns because they are
the two stable axes — grouping the rendered catalogue, and reading a wardrobe
back on the CLI.

**Why `item_index`.** Most photographs will be one garment, but a rail of
shirts or a folded stack is a natural thing to shoot, and a schema that
assumes 1:1 silently discards everything but the first item. The vision call
therefore returns a *list*, usually of length one, and each entry becomes a
row. One loop and one integer column, and the failure mode is gone rather
than latent.

**Wardrobe scoping is per member,** keyed on `member_sub`. The album id is a
per-member field in `family.yaml` — `wardrobe_album`, beside `timezone` — not
a setting and not an environment variable. It is per-member, non-secret
configuration whose change history should be a pull request, which is
precisely what `family.yaml` is for and says it is for in its own header
comment. `catalog.py` reads it from the resolved member and passes it to
`immich.album_assets`; `eve-tools` is handed an album id and never needs to
know whose it is. A member with no `wardrobe_album` has no wardrobe, and
`read_wardrobe` says so rather than erroring.

Hardcoding a single album would cost the same to build and be wrong the first
time Kendra asks.

## The vision pass

`get_model(Tier.REFLEX).with_structured_output(WardrobeItems)` — the third
use of a pattern `src/eve/memory/extract.py:263` and `src/eve/suggest.py:176`
already establish.

REFLEX rather than MECHANICAL because `models.py` says so in as many words:
that tier rides the metered Google key specifically so high-volume grinding
does not consume the ChatGPT subscription limits Noah uses for his own work.
Cataloguing a hundred-garment wardrobe is exactly that shape of work.

The prompt is `prompts/wardrobe.md`, joining `eve.md`, `extract.md`,
`suggest.md` and `ambient_filter.md`, so what Eve notices about a garment is
tunable in a prompt file rather than in code.

## Sync

**One function, two callers.** `eve-wardrobe sync` on the CLI, and a
`sync_wardrobe` tool on the stylist. The CLI is what the initial bulk load
has to use — a hundred photographs is never going inside a conversational
turn — and the tool is what makes "Eve, I added some clothes" work for
someone who is never going to open a terminal.

An ambient tick was considered and rejected. Scheduling would be free, the
poll loop already exists (`src/eve_ambient/app.py:171`), but ambient's job is
deciding what is worth interrupting a person about. "I catalogued a jumper"
is not a signal, and putting a non-signal into `SOURCES` would bend what that
registry means for the convenience of a cron.

**Idempotent by asset id.** Already-catalogued assets are skipped; assets
that have left the album have their rows deleted; `--force` re-catalogues
everything, which is what you want after rewriting `prompts/wardrobe.md`.

**Staleness is visible, not silent.** `read_wardrobe` compares the album's
asset count against the row count and, when they differ, says so in its
result. One extra API call, no vision, no measurable latency — and the
alternative is a stylist that confidently dresses you out of a wardrobe
missing the coat you bought last week.

## Scoped skills

This section changes shared infrastructure and lands for Home, Mail and
Finances as well as the stylist. It is in this spec because the stylist is
the first thing that needs it, not because it is stylist-specific.

**Ownership goes in frontmatter.** `parse_skill_text`
(`src/eve/skills/registry.py:37`) already reads YAML frontmatter for `name`
and `description`; it gains `specialist`:

```yaml
---
name: dress-for-the-day
description: How to assemble an outfit from a catalogued wardrobe.
specialist: stylist
---
```

Absent means the skill is Eve's, which is what every existing file including
`greet-warmly` gets for free. Present means it belongs to that specialist
alone. One corpus, one glob, one loader — `Skill` gains a
`specialist: str | None` field and `load_skills` fills it. A directory
convention (`skills/stylist/*/SKILL.md`) was the alternative; frontmatter
costs less because the parser already reads frontmatter.

**Two searches, filtered oppositely.** Eve's existing `search_skills` drops
anything with a `specialist` set, so she never sees them. A new
`build_skills_search(name)` factory returns a specialist's own version,
matching only skills whose `specialist` equals its name — strictly its own,
not its own plus the unscoped pool. If a specialist turns out to want the
shared pool too, that is a one-word change.

**`build_specialist` appends it to every specialist's tool list**, so the
capability arrives with the mechanism rather than as four separate wirings. A
specialist with no skills of its own searches an empty set and gets "no
matching skill", which is the correct answer.

**The specialist version returns procedure text only — a plain string tool,
no `Command`.** Eve's `search_skills` returns a `Command` because half its
job is appending `DynamicToolSpec`s to `dynamic_tools` in `EveState` for
materialization on the next model call. Inside a specialist the loop is
`create_agent`'s own message state, not `EveState`, and there is no
rebinding step to receive a spec. So MCP, sandbox and authored-procedure
matches are excluded there, and the tool is roughly twenty lines over
`rank_skills`.

**Knowledge crosses the boundary; capability does not.** If a specialist ever
needs a dynamically-bound tool, that is a real design problem deserving its
own ticket, not something to smuggle in behind a skills search.

## Behaviour

### The prompt

`prompts/stylist.md` carries operating discipline, mirroring `home.py`'s
("you do not know which entities exist — call `list_entities` first"): read
the wardrobe before recommending anything; check weather and calendar unless
the member has already supplied the occasion; name specific catalogued items;
give one recommendation with a one-line reason and at most one alternative.

### Never name a garment you haven't seen

The single most important line in the feature. A stylist that confidently
recommends a coat you don't own is worse than no stylist, and because Eve
cannot show you the photograph, you have no way to catch it except by walking
to the wardrobe and finding nothing there.

### The heuristics

`skills/dress-for-the-day/SKILL.md`, scoped `specialist: stylist`. A starting
set to edit rather than a finished theory of dress: dress for the day's
coldest relevant moment rather than its average; let the most formal thing on
the calendar set the floor; anchor on one item and build outward; one
statement piece at most; layer across a temperature range rather than for its
midpoint.

These live in a skill rather than the prompt because that is what the skills
mechanism is for, and because a scoped skill is now something the stylist can
actually reach. Note that this buys no runtime editability over the prompt
file: both are baked into the image and both need a deploy to change. Only
Eve-*authored* procedures, which are database rows, are live-editable.

## Failure and degradation

Every case takes the shape this repository already uses — a tool returns a
string explaining itself, the loop continues, and Eve tells the member the
truth.

- **Empty catalogue.** `read_wardrobe` says so; the stylist tells you to add
  photographs and sync. It does not improvise a wardrobe.
- **Stale catalogue.** The count mismatch note; the stylist may call
  `sync_wardrobe` itself.
- **Immich unreachable.** `eve.tools_client.invoke` already degrades to an
  `error:` string; the stylist reports that it cannot see the wardrobe rather
  than guessing at one.
- **No `calendar.read`.** `permission_denial` returns into the loop and the
  stylist dresses on weather alone.
- **Weather unavailable.** Proceed, and say what was assumed.
- **Loop exhausted.** `base.py:_loop_exhausted` already covers it; no new
  handling.

## Known ceilings

Marked in the code with `ponytail:` comments rather than solved now.

- **`read_wardrobe` returns the entire catalogue in one string** — roughly 6k
  tokens at two hundred garments, fine for a MECHANICAL call, and enumeration
  is the whole reason a catalogue beat CLIP search. Past about three hundred
  items the tool grows a category filter.
- **A specialist's skills search reads the filesystem corpus only** — no
  Eve-authored database rows, which would mean a round trip inside every
  specialist loop.
- **`rank_skills` re-embeds every candidate's description on every call**
  with no cache (`src/eve/skills/search.py:44`). This is pre-existing.
  Scoping shrinks each call's candidate set, so it gets slightly better
  rather than worse.

## Observability

Following the discipline of `specialists/base.py` and `memory/recall.py`,
where each attribute exists to answer a specific question with a number:

- `eve.wardrobe.items_catalogued` and `eve.wardrobe.vision_failures` on a
  sync — is the vision pass actually working, and on what fraction.
- `eve.wardrobe.stale_count` on `read_wardrobe` — is the catalogue drifting
  in practice, or is the CLI enough.
- `eve.skills.specialist_search_used`, by specialist — is the scoped-skills
  mechanism used at all, or is it the MCP registry all over again.

## Testing

Following the existing tiers:

- **Unit, fake model.** The stylist loop through the `_model_for_test`
  indirection `home.py` uses. Permission denial before the model is
  constructed, matching the existing assertion in `base.py`'s tests.
- **Unit, fake Immich and fake vision model.** The sync: a multi-garment
  photograph, an asset deleted from the album, a re-run being idempotent,
  `--force` re-cataloguing.
- **Unit.** Scoped-skill filtering in both directions — a scoped skill absent
  from Eve's search, an unscoped skill absent from a specialist's, and a
  specialist with no skills getting a clean empty result.
- **Live.** One real Immich call behind the existing live-test convention, to
  catch the album response shape flagged above.

## Definition of done

1. `eve-wardrobe sync` catalogues a real Immich album into
   `eve_wardrobe_item`, and `eve-wardrobe list` prints it back readably.
2. Asking Eve what to wear on a real day returns a specific outfit, naming
   real garments from the catalogue, informed by the real forecast and the
   real calendar.
3. A member without `wardrobe` is refused at the Eve → stylist edge; a member
   with `wardrobe` but not `calendar.read` gets an outfit chosen on weather
   alone.
4. A garment added to the album and synced appears in the next
   recommendation; one removed stops appearing.
5. Eve's `search_skills` does not return `dress-for-the-day`.
6. Home, Mail and Finances each gain a working scoped-skills search, and each
   returns a clean empty result until someone writes them a skill.

## What this deliberately does not do

- **Shopping.** Its own issue, per "What this is not".
- **Laundry state, wear tracking, what you wore yesterday.** Each needs a
  write path and a habit to maintain it; none is required for a good first
  recommendation.
- **Showing photographs.** Blocked on the client's closed component
  catalogue, in another repository.
- **Outfit memory.** Eve does not record what she recommended or whether you
  took the advice. Worth revisiting once there is evidence the
  recommendations are good enough to be worth learning from.
- **A general calendar specialist.** Real value, separate ticket.
- **Dynamically-bound tools inside a specialist.** Explicitly out, per
  "Scoped skills".
