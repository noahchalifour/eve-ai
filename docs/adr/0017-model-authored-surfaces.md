# 17. The model authors surface structure; the server owns the envelope

**Status:** Accepted
**Date:** 2026-09-02
**Supersedes:** [ADR 0014](0014-dynamic-ui-is-server-built.md)

## Context

ADR 0014 decided that the model's only decision is WHETHER a surface is the
right answer: it called a no-argument `show_weather`, and `eve.ui.weather`
assembled everything in the card server-side. Every new kind of surface was
therefore a new `show_{thing}` tool plus a new builder, authored by a human.
A member who wants a workout tracker cannot have one, and never could.

## Decision

One tool, `show_surface(components)`. The model authors the component tree.
The server mints the surface id, sets `catalogId`, validates against the
client mirror, and returns the diagnostic when it rejects.

ADR 0014 gave three reasons not to let the model emit surface JSON. They are
not equally durable:

1. **"The client rejects silently."** Solved. That was an objection about a
   missing feedback channel. `eve.ui.protocol` already mirrors the client's
   validator; returning its code as the tool result turns the invisible drop
   into a correction the model acts on inside the existing tool loop. The
   rejection carries the legal properties for the types the model actually
   used, so the retry needs no second skills lookup - `rank_skills` is
   semantic and can miss, and correctness must not depend on it twice.
2. **"A model asked for a forecast will produce one whether or not it has
   data."** Accepted as a trade. It was always about DATA, not structure, and
   it does not apply to input surfaces: a workout tracker has nothing to
   invent because the member types every value.
3. **"The surface JSON would occupy the model's own context."** Mostly
   resolved, by moving the catalog into `skills/build-a-ui/SKILL.md` where
   `search_skills` retrieves it on demand. Nothing UI-specific sits in the
   tool list on turns that build no UI. What remains is the tree the model
   authored, in that turn's tool call; `strip_frames` cannot help, because
   it is a tool argument rather than message content.

## Consequences

**There is no UI-specific data path.** A `read_data(source)` tool was
designed and cut: the only surface needing it was the forecast strip, which
this ADR deletes, and it would have added a top-level tool AND a permission
registry - every existing path to eve-tools is gated by a specialist, and an
open `read_data` would route around that. Data reaches a surface through the
tools Eve already has, which are prose. A composed card can therefore
transcribe a number wrong. That is the trade.

**`show_surface` takes no `data`.** Nothing produces `$data.` bindings, so
nothing can hit the failure they carry - an unresolvable binding renders the
WHOLE-surface fallback, not a partial tree. The resolver stays on both sides
as mirror code with no producer, along with patch support, because the client
still implements both and removing them touches four validator copies for no
behavioural gain.

**The 8-surface cap lost the premise that made it safe.** ADR 0014 conceded
that "6 rounds is below 8 surfaces" counts ROUNDS and assumes one call per
round, and rested on `show_weather` being "one no-argument tool a model has
no reason to call more than once". `show_surface` is a tool a model has
plausible reason to call more than once, and parallel batching is now
encouraged. Still deferred, on narrower grounds: the catastrophic case is
already prevented, since nine creates in one frame makes the client reject
the WHOLE frame and `persist_ui` trims to eight and says so. What remains is
that the live `custom` stream is uncapped, so a client can briefly render
more surfaces than a reopened transcript shows - cosmetic and transient.

**A counter derived from `state["messages"]` cannot fix that.** Parallel
siblings execute against the same state snapshot, so each reads zero surfaces
already emitted and every one passes. The idiom `_tool_rounds_this_turn` and
`persist_ui` both use does not extend here. Any real fix is run-scoped
mutable state in `eve.ui.stream`, which is a good part of why it is not worth
building for a cosmetic bound.

**The envelope's `state` IS trusted**, inverting ADR 0014's rule that the
envelope is never trusted and is re-read from Home Assistant instead. There
is nothing to re-read: the member's typed values are the source of truth.
`validate_json_value` still caps every string, and `readable_submission`
strips frame markers from typed text.

**`ui_action` is gone, and with it the codebase's only raising node.** ADR
0014's caveat that "`ui_action` raises where the rest of Eve returns a
string" is retired rather than given a second instance. A submit routes to
`ui_submit`, which rewrites the envelope into a sentence and continues to
`recall` - a submit has no predetermined answer, and Eve is about to decide
where to put something, which is when she wants memory context.

**The catalog now has five hand-synced copies**, up from four: the server
validator, three client layers, and the skill. The skill is prose, so no
validator can catch it drifting; `tests/test_skills_build_a_ui.py` asserts it
against `protocol._ALLOWED_PROPERTIES` instead.

**Old threads lose their weather cards.** Frames from before this change stay
in the transcript and now render the neutral fallback, because
`weather_surface.dart` is deleted.
