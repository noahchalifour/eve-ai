# Model-authored dynamic surfaces

**Status:** Design approved
**Date:** 2026-09-02
**Issue:** [EVE-22](https://linear.app/chalifour-development/issue/EVE-22/dynamic-assistant-ui-needs-to-be-more-flexible)
**Supersedes:** [ADR 0014](../../adr/0014-dynamic-ui-is-server-built.md)

## The ask

"I want to be able to just ask the agent to build me a UI for something."

Today that is impossible by design. ADR 0014 decided that the model's only
decision is *whether* a surface is the right answer: it calls a no-argument
`show_weather`, and everything in the card is assembled server-side by
`eve.ui.weather` from Home Assistant's own forecast. Every new kind of
surface is therefore a new `show_{thing}` tool plus a new server-side
builder, authored by a human in a pull request. A member who wants a workout
tracker cannot have one.

The worked example driving this design:

> I ask for a workout tracker. I can type my reps, weight, RPE. Those need
> inputs, and some buttons that don't call Eve again. Then a Save button at
> the bottom — when I tap that, the logged workout gets sent to Eve to save
> so she remembers this workout in the future.

## What was already possible

Two findings shaped the design, both verified against the client:

**The client already renders arbitrary component trees.**
`dynamic_surface_renderer.dart` has generic builders for all twelve
non-weather types (`column`, `row`, `card`, `list`, `grid`, `divider`,
`text`, `icon`, `badge`, `button`, `segmentedSelection`, `expandable`), and a
surface's `catalogId` is checked against the same shared ID set as a
component's `type`. A surface with `catalogId: "card"` and a hand-built tree
renders today with no client change. Only `weather` had a bespoke widget.

**The blockers were interactivity and inputs.**
`dynamic_surface_protocol.dart:254` was literally
`value == 'weather.rangeChanged' ? null : 'action-schema'`, so no
model-built control could do anything. There were no input component types
at all, and `localState` was written *only* by the server via patches —
nothing in the client mutated it from user interaction.

## Decision

**One tool, forever: `show_surface(components)`.** The model authors the
component tree. The server owns the envelope, validates the tree, and reports
rejections back to the model. The tool count stops growing with the catalog.

### Why the model may now author structure

ADR 0014 gave three reasons not to let the model emit surface JSON. They are
not equally durable, and this design treats them differently:

1. **"The client rejects silently."** *Solved.* The ADR's strongest objection
   was about a missing feedback channel: an illegal type or an undeclared
   property renders one neutral "This content can't be shown" card, and on
   the `custom` path is dropped with a log line that never leaves the phone.
   `eve.ui.protocol` already mirrors the client's validator. Returning its
   diagnostic code to the model as the tool result converts the invisible
   drop into a correction signal, and the model retries inside the existing
   tool loop.

2. **"A model asked for a forecast will produce one whether or not it has
   data."** *Accepted as a trade.* This was always an objection about
   *data*, not structure, and it is real: a card composed from what a prose
   specialist said can transcribe a number wrong. It does not apply at all to
   input surfaces, which is the driving use case — a workout tracker has
   nothing to invent because the member types every value.

3. **"The surface JSON would occupy the model's own context."** *Mostly
   resolved.* The catalog reference lives in `skills/build-a-ui/SKILL.md` and
   is retrieved on demand, so nothing UI-specific sits in the tool list on
   turns that build no UI. What remains is unavoidable: a tree the model
   authored is in that turn's tool call, and `strip_frames` cannot help,
   because it is a tool *argument* rather than message content.

### Data comes from the tools Eve already has

There is no UI-specific data path. The model uses `ask_home`, `ask_mail`,
`ask_finances`, `ask_health`, `search_memory`, `search_skills` and any
dynamic tool, reasons about what it got, and composes a surface from it.

A `read_data(source)` tool was designed and rejected. It would have given the
model structured JSON from any eve-tools handler, preserving ADR 0014's
no-model-authored-data guarantee. It was cut because the only surface that
needed it was the forecast strip, which this design deletes: it would have
added a top-level tool *and* a permission registry (every existing path to
eve-tools is gated by a specialist — `ask_home` enforces `home.control` via
`build_specialist` — and an open `read_data` would route around that) to
serve approximately one card.

### `show_surface` takes no `data`

The model inlines literal strings and numbers directly into component
properties. `$data.` bindings existed to avoid repetition and to be patch
targets, and patches die with `ui_action` (below).

This removes a whole failure class rather than mitigating it: an unresolvable
binding renders the *whole-surface* fallback, not a partial tree, and nothing
model-authored can hit that if nothing model-authored carries bindings.
`DynamicSurfaceCatalog.resolveBinding` and the server's `_BINDING` regex stay
as existing mirror code; they simply have no producer.

### The server owns the envelope

The model supplies `components` and nothing else. `eve.ui.surface` mints the
`surfaceId` and always sets `catalogId: "column"` — the client's non-weather
path already wraps the tree in a raised card. Two fewer fields the model can
get wrong.

## The wire contract

`catalogVersion` stays at `"1"`. The changes are additive component ids, and
`stream.supports()` gates per-id off the client's declared `catalogIds`, so a
phone on an old build simply never gets the new types. Bumping the version
would instead break *every* surface on every un-updated phone, because
`supports()` does strict equality on it. Eve deploys from Kubernetes and the
app ships to phones independently, so that skew is real. `catalogVersion` is
reserved for a breaking change to an existing type.

### Two new component types

| type | properties | notes |
|---|---|---|
| `textField` | `stateKey`, `label` | `stateKey` is a `localState` key |
| `numberField` | `stateKey`, `label` | same |

Both read their initial value from `localState[stateKey]` and write it back
on change. `min`/`max` are deferred along with `stepper`.

`stateKey`, not `name`: `_validate_property` dispatches on property name
alone, `name` already belongs to `icon`, and there it routes through
`_string_or_binding` — which a state key must never be, since it is a literal
key. Threading `component_type` into `_validate_property` would mean
reworking the Dart validator identically, as it switches on `entry.key` the
same way. Renaming the property avoids both.

### Two kinds of button

A `button` declares **exactly one** of:

- `setState` — a literal JSON object merged into `localState` on tap. No
  expressions, no arithmetic, no evaluator. Covers presets, clearing a form,
  and toggles.
- `actionId: "surface.submit"` — dispatches to Eve as a turn.

Both or neither is a `component-schema` rejection. This is the one check that
needs the component type, and it lives in `_validate_properties`, which
already has it.

**Increments are not expressible.** "Add another set" needs arithmetic in the
protocol or the deferred `stepper`. Ship literal-merge only; the member types
the number.

### `segmentedSelection` gains local mode

With `setState` present it writes `localState` instead of dispatching. This
is required, not optional: its only consumer today is the weather toggle,
which this design deletes, and the only dispatchable action left is
`surface.submit`, which is meaningless on a selector. Without local mode the
component becomes dead, and pick-one would need a new type.

### Actions

`ACTION_IDS` becomes `{"surface.submit"}` — still a closed set, no longer a
single hardcoded literal. The action envelope gains `state`, carrying the
surface's `localState` at tap time. `actionValue` stays scalar and untouched.

**One rule inverts.** ADR 0014 says the envelope's `data` is never trusted
and the range is re-read from Home Assistant instead. For a submit there is
nothing to re-read: the member's typed values *are* the source of truth.
`validate_json_value` still caps every string at 2,048 characters, and the
readable rewrite strips frame markers from typed text — a member typing
`</assistant-ui>` is self-only blast radius, but it should not reach the
transcript intact.

## Server (`eve-ai`)

**`eve/ui/protocol.py`** — add `textField` and `numberField` to `CATALOG_IDS`
with their `_ALLOWED_PROPERTIES` entries; add `setState` to `button` and the
exactly-one-of rule; `ACTION_IDS` becomes `{"surface.submit"}`. Remove
`weather` from `CATALOG_IDS`, its `_ALLOWED_PROPERTIES` entry, and
`location`/`condition` from `_STRING_PROPERTIES` along with `temperature`'s
`_number_or_binding` branch.

`_validate_patch` becomes unused — nothing emits patches once `ui_action` is
gone — but stays as client-mirror code, since the client still supports them.

**`eve/ui/surface.py`** *(new)* — the builder, filling the role `weather.py`
did: takes the model's component list, mints the surface id, sets
`catalogId`, returns the create operation.

**`eve/ui/tools.py`** — `show_surface(components)`, same
`response_format="content_and_artifact"` shape as `show_weather` had, so
`persist_ui` gives it durability for free with no change to `persist.py`
(`_is_operation` already accepts any structurally valid operation).

Two decisions inside it:

- **Per-type capability gating.** `stream.supports` widens to take a set of
  ids, and `show_surface` checks every type present in the model's tree
  against the client's declaration. An old build still gets a text/card/badge
  summary — it can genuinely render those — and is refused only trees
  containing inputs.
- **Rejection returns the diagnostic code plus the legal properties for the
  types actually present in the rejected tree.** `validate_operation`'s
  return values are the client-mirror contract and `test_ui_protocol.py`
  asserts them exactly, so the validator itself is not enriched; the tool
  composes the hint from `_ALLOWED_PROPERTIES`, which it already has. Scoping
  it to the types the model actually used keeps it near 50 tokens and, more
  importantly, makes the retry path **self-sufficient** — it does not depend
  on a semantic retrieval succeeding (see the skill below). Retries are
  bounded structurally by `EVE_MAX_TOOL_LOOP_ITERATIONS` (6) — no new
  counter.

**`skills/build-a-ui/SKILL.md`** *(new)* — the catalog reference and the
UI-building guidance, retrieved on demand by `search_skills` rather than
carried in the tool list on every turn.

This is what `search_skills` is for, in its own words: "a SKILL.md match
returns a procedure directly as the tool's result — knowledge, not a new
capability, so nothing about the bound-tool list changes." A matched
procedure is returned as full content (`f"# {m.name}\n{m.content}"`), and
filesystem `SKILL.md` files load unconditionally — `self_authoring_enabled`
gates only Eve-authored rows, so this does not ride on a setting.

It holds more than a schema could justify in a docstring: the per-type
property table, the `stateKey` contract, the two kinds of button, and actual
guidance — when a surface beats prose, how to keep a tree phone-sized, when
a form is the wrong answer. Editable without a code change.

`show_surface`'s docstring shrinks to what it does, that the tree must
validate, and that the catalog lives in a skill worth searching for first.

**The retrieval can miss, and correctness does not depend on it.**
`rank_skills` is a semantic top-3 over `description` embeddings across the
whole corpus, MCP tools included, so a UI request is not guaranteed to
surface this skill — and as the corpus grows, less so. A miss means the model
calls `show_surface` having guessed the schema. That is why the rejection
hint above is self-contained rather than a pointer to the skill: guess →
rejected with the legal properties for the types used → correct retry, with
no second retrieval in the loop. The skill improves the *first* attempt and
carries the taste; the rejection hint guarantees the *second*.

**It costs one extra tool round when it does hit.** `search_skills` then
`show_surface`, plus a data tool if the surface needs one, plus a possible
retry — four of the six rounds in the worst case. Within budget, with less
margin than before. Worth watching rather than pre-solving.

## Parallel tool calls

`search_skills` and a data tool should be issued in the **same** round. This
needs no plumbing: `ToolNode._afunc` already runs a round's calls through
`asyncio.gather` (`tool_node.py:858`), and `_combine_tool_outputs` explicitly
supports batching a `Command`-returning tool — which `search_skills` is, since
it updates `dynamic_tools` — with plain-string tools, wrapping the latter as
`{messages: [...]}` and letting LangGraph apply the list of updates.

Only `show_surface` is genuinely sequential; it needs both results. So the
worked path is `[search_skills ∥ ask_home] → show_surface`: two rounds instead
of three, or three instead of four when a rejection forces a retry. The win is
budget headroom as much as latency, since `_tool_rounds_this_turn` counts
rounds rather than calls.

What this needs is guidance, in `skills/build-a-ui/SKILL.md` and
`prompts/eve.md`: search for the catalog and gather the data together, then
build.

### ADR 0014's deferred surface counter

Encouraging parallel batches retires the argument that made the 8-surface cap
safe. ADR 0014 conceded that "6 rounds is below 8 surfaces" counts *rounds*
and assumes one call per round, and rested on the fact that `show_weather` was
"one no-argument tool a model has no reason to call more than once" — then
named its own trigger: "if a model starts calling `show_weather` more than
once a turn, that reasoning stops holding and a real per-run counter is what
to add."

`show_surface` is a tool a model has plausible reason to call more than once,
so that premise is gone and needs replacing rather than inheriting.

**Still deferred, on narrower grounds.** The catastrophic failure is already
prevented: nine creates in one frame makes the client reject the *whole*
frame, and `persist_ui` trims to eight and logs it. What remains is a
divergence that exists today — the live `custom` stream is uncapped, so a
client can briefly render more surfaces than a reopened transcript shows.
That is cosmetic and transient, and nine surfaces from one turn is
implausible even with parallel calls.

**One finding for whoever does fix it.** A counter derived from
`state["messages"]`, the idiom `_tool_rounds_this_turn` and `persist_ui` both
use, **cannot work here.** Parallel siblings execute against the same state
snapshot, so each one reads zero surfaces already emitted and every one
passes. The fix has to be run-scoped mutable state in `eve.ui.stream`, not a
message-derived count — which is a substantial part of why it is not worth
building for a cosmetic bound.

`persist_ui` itself needs no change: it reads `ToolMessage` artifacts back to
the last `HumanMessage`, so it collects a parallel batch correctly, and the
batch's order is `tool_calls` order and therefore deterministic.

**`eve/ui/actions.py`** — `parse_action` accepts `surface.submit` and reads
`state`. `ui_action` and `UiActionError` are deleted. New `ui_submit` node:
rewrites the raw envelope into a readable sentence
(`"I filled in the workout form — Exercise: Bench press · Reps: 8 · Weight:
185 · RPE: 8"`), reusing the same-id `HumanMessage` replacement `ui_action`
used so a reopened transcript shows prose rather than a JSON bubble, then
hands off to the normal path.

**`eve/graph.py`** — `_route_after_context` routes a `surface.submit`
envelope to `ui_submit`, which edges to `recall`. Submit deliberately keeps
`recall`, unlike a weather tap did: Eve is about to decide where to save
something and wants memory context for it. The `weather.rangeChanged` branch
and the `ui_action` node are removed. `show_surface` replaces `show_weather`
in `_static_tools`.

**`prompts/eve.md`** — when to build a surface versus answer in prose. The
weather-card guidance (lines 22-25) is removed.

## Client (`flutter-open-assistant`)

The largest piece, because `localState` is currently write-only from the
server.

**Local state becomes client-mutable.** `DynamicSurfaceDefinition.localState`
is immutable and only ever replaced by a server patch. The renderer gains an
`onLocalStateChanged(surfaceId, patch)` callback alongside `onRemoteAction`,
routed to `AssistantSessionUseCase`, which merges and writes through
`DynamicSurfaceCache`. That last part is nearly free and worth having:
`_mergeCachedLocalState` already restores `localState` and *only*
`localState` on reopen, so typed-but-unsubmitted form values survive a
relaunch on machinery that exists for exactly this.

**Renderer additions.** `textField` and `numberField` widgets reading initial
values from `localState[stateKey]` and writing on change; `setState` handling
on `button` as a literal map merge; `segmentedSelection`'s local mode.

**The `actionId` gate opens.** `dynamic_surface_protocol.dart:254` becomes a
closed set containing only `surface.submit`. `UiAction` gains `state`,
carrying `localState` at tap time.

**Deletions.** `weather_surface.dart`, `_buildWeather` and the
`catalogId == 'weather'` branch, `weather` from all three client ID lists,
and `weather.rangeChanged`.

**Five catalog copies stay hand-synced.** `DynamicUiCapabilities.v1`,
`DynamicSurfaceProtocol`, `DynamicSurfaceCatalog`, and the server's
`eve.ui.protocol` each declare the catalog independently, once per layer that
must not import the others. The client's own comment says to keep them in
lockstep by hand. This change does not fix that; it pays it four times — and
adds a fifth in `skills/build-a-ui/SKILL.md`, which is prose and therefore
the only copy no validator can catch drifting. That one is covered by a test
asserting it against `protocol._ALLOWED_PROPERTIES` instead.

## Error handling

Unchanged and already correct. `show_surface` returns a string on failure per
the codebase-wide rule — a rejected tree returns its diagnostic code so the
model retries; nothing raises.

Deleting `ui_action` removes the codebase's **only** raising node, so ADR
0014's "`ui_action` raises where the rest of Eve returns a string" caveat
disappears rather than gaining a second instance.

A submit arriving from a client that declared no capabilities routes to
`recall` as ordinary speech, matching the existing fail-closed branch. The
readable rewrite still applies, so the member gets a normal answer instead of
a JSON bubble.

## Testing

**Server.** `test_ui_protocol.py` gains the two component schemas, the button
exactly-one-of rule, and `surface.submit`, and drops the `weather` cases.
`test_ui_tools.py` covers per-type capability gating and a rejected tree
returning its diagnostic code. `test_ui_actions.py` swaps `ui_action`
coverage for `ui_submit`'s rewrite, including a member typing frame markers
into a text field. `test_ui_persist.py` needs no change.
`test_ui_stream.py` covers `supports` taking a set. One graph test batches
`search_skills` with a plain tool in a single round and asserts both results
land — the `Command`/plain-string mix is the part worth pinning, since it is
library behaviour this design now depends on. A skills test asserts
`build-a-ui` parses and that its property table matches
`protocol._ALLOWED_PROPERTIES` — a fifth copy of the catalog that would
otherwise drift silently, and the only one no validator checks.

**Client.** `dynamic_surface_protocol_test.dart` and
`dynamic_surface_renderer_test.dart` gain the new types and drop weather;
`weather_surface_test.dart` is deleted; a use-case test covers local state
surviving a reopen through the cache.
`integration_test/dynamic_weather_flow_test.dart` is replaced by a
form-submit flow test.

## Costs, accepted rather than solved

**A retrieval that can miss.** The catalog moved out of the tool list into
`skills/build-a-ui/SKILL.md`, which resolves ADR 0014's third objection
rather than paying it — nothing UI-specific sits in context on turns that
build no UI. The round it costs is absorbed by issuing `search_skills`
alongside the data tool (see Parallel tool calls), so what remains is the
retrieval itself: a miss leaves the model guessing on its first attempt, and
the rejection hint is what recovers it.

**A composed card can transcribe wrong.** Data reaches a surface through the
model, from prose tools. ADR 0014's second objection, traded deliberately.

**Increments and bounded numeric inputs are missing.** `stepper`, `slider`,
`checkbox` and `select` are deferred. Revisit if the literal-merge ceiling
actually bites.

**Nothing produces `$data.` bindings or patches.** Both remain in the
protocol and the renderer as mirror code with no producer. Left in place
because the client supports them and removing them touches four validator
copies for no behavioural gain.

## Definition of done

- A member can ask Eve for a workout tracker and get one: labelled text and
  number inputs, a Save button.
- Typing into a field mutates only client state — no server round trip, and
  the values survive a relaunch.
- A non-submit button merges literal state locally with no round trip.
- Save sends the form's values to Eve as a turn; she chooses where they go,
  and the transcript shows a readable sentence rather than JSON.
- A surface survives a session reopen via the persisted `<assistant-ui>`
  frame, unchanged from today.
- An invalid tree returns its diagnostic code plus the legal properties for
  the types it used, and the model retries within the tool loop rather than
  failing the turn — without needing to retrieve the skill to do it.
- `search_skills` surfaces `build-a-ui` for a plausible UI request, and
  `show_surface`'s docstring carries no property table.
- `search_skills` and a data tool issued in one round both resolve, with the
  `Command` and plain-string results merged correctly.
- A client declaring only the twelve original ids gets non-input UIs and is
  refused input trees, with Eve answering in prose instead.
- `show_weather`, `eve/ui/weather.py`, `ui_action`, `weather_surface.dart`
  and the `weather` type are gone from all four catalog copies.
- ADR 0017 records the supersession.
