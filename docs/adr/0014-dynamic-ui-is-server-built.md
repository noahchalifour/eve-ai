# 14. Dynamic UI surfaces are built server-side and only triggered by the model

**Status:** Accepted
**Date:** 2026-08-31

## Context

The Flutter client renders agent-declared interactive surfaces inline in a
chat turn - a weather card today, over a closed thirteen-type catalog
(`assistant-ui/1.0`). A provider drives it by sending create/patch/delete
operations, either natively on LangGraph's `custom` stream mode or as a
portable `<assistant-ui>` frame embedded in assistant text.

The obvious implementation is to teach the model the protocol and let it emit
surface JSON. That fails three ways at once. The client validates hard and
rejects **silently**: an illegal component type, a property the type does not
declare, a malformed `$data.` binding, or a `temperature` that resolves to a
string all render one neutral "This content can't be shown" card, or on the
native path are dropped with a log line that never leaves the phone. Second,
a model asked for a forecast will produce one whether or not it has data -
which is a card confidently displaying invented weather. Third, the surface
JSON would occupy the model's own context on every turn it appears in.

## Decision

The model's only decision is **whether** a surface is the right answer. It
calls a no-argument `show_weather` tool. Everything in the card -
structure, bindings, data, forecast cells, labels - is assembled by
`eve.ui.weather` from Home Assistant's own `weather.get_forecasts` response,
validated against a server-side mirror of the client's validator
(`eve.ui.protocol`), and only then emitted.

Consequences of that shape, each deliberate:

- **Capabilities gate the tool, not just the emission.** The client declares
  what it can render at `config.configurable.assistant_ui`, and
  `show_weather` is bound into the model's tool list only when that
  declaration names the `weather` catalog. Failing closed is right because a
  surface is not free: a frame emitted at a client that cannot render it
  stays in that thread's transcript permanently.
- **Every operation is streamed on `custom` AND written into the AI message
  as a portable frame.** `custom` frames are streamed and never stored; the
  client replays a reopened session from `values.messages` alone. Two
  mechanisms is not redundancy - it is one for live rendering and one for
  durability, and they do not collide because LangGraph's `messages` stream
  mode carries LLM token events only, so a node's message rewrite is invisible
  to the live client.
- **The action round trip has no model in it.** A tap arrives as the next
  turn's user text, so `load_context` routes an action envelope to a
  `ui_action` node that re-reads the forecast and emits one patch. It skips
  `recall` (an embedding call for a tap) and `extract` (whose input would be a
  JSON envelope), and the raw envelope is replaced in the transcript with a
  readable sentence.
- **The envelope's `data` is never trusted.** It arrives from the client and
  the requested range is re-read from Home Assistant instead.

## Consequences

**The validator is a second copy of a client-side one** (`eve.ui.protocol`
mirrors `dynamic_surface_protocol.dart`), and it will drift the day the
catalog grows. That is accepted because the client rejects silently: without
this copy there is no server-side signal that a surface was ever refused. The
client already carries the same duplication three times over by design -
domain, data layer, renderer - and its own comment says to keep them in
lockstep by hand.

**The card shows the home's weather and nothing else.** There is one HA
weather entity, `show_weather` takes no location, and Eve is told in
`prompts/eve.md` not to offer a card for another city. A member asking about
Toronto while away from home gets prose. Adding a second location means adding
a data source, not a tool argument.

**Per-turn and per-minute protocol limits are structural, not counted.**
`show_weather` emits at most one surface per call and the outer tool loop is
bounded to `EVE_MAX_TOOL_LOOP_ITERATIONS` (6) rounds, which is below the
8-surface ceiling; one action turn emits one patch and requires one human tap,
which cannot reach 30 updates a minute. `eve.ui.persist` enforces the
8-surface cap explicitly, and says so in a log line, because the ninth create
in one frame makes the client reject the whole frame. That "6 rounds is below
8 surfaces" argument counts ROUNDS, and quietly assumes one tool call per
round; a round can in principle carry PARALLEL tool calls, so nine or more
`show_weather` calls returned in a single round - and so nine or more creates
in one turn - is reachable even today, just very unlikely with one
no-argument tool that a model has no reason to call more than once. Worth
naming rather than fixing in code: only the persisted frame is trimmed to the
cap (`persist.py`'s own 8-surface slice); the live `custom` stream that
renders the cards is uncapped, so a client could briefly render more surfaces
live than a reopened transcript ever shows. If a future surface type emits in
a loop, or a model starts calling `show_weather` more than once a turn, that
reasoning stops holding and a real per-run counter is what to add.

**`ui_action` raises where the rest of Eve returns a string.** Every other
external call in this codebase degrades to a returned error string, because
its result goes to a model that can talk around it. There is no model in that
branch, and the protocol's own failure contract is an error event: an
exception becomes an SSE `error` frame, the client marks the surface `error`
with its last valid data retained and offers a retry. Returning quietly would
leave the card spinning on "Loading forecast" with nothing to say why. The
cost is that a failed action turn leaves the raw envelope in the thread's
history, where it renders as a user bubble of JSON.
