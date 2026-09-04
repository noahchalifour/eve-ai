# 18. Openers are a thread-free, chip-only run

**Status:** Accepted
**Date:** 2026-09-03

## Context

ADR 0013 gave a finished turn 2-4 reply chips. It deliberately produced
nothing for the screen a member actually lands on: an empty chat, which had
no exchange for `suggest` to continue and therefore got `[]` by design.

The Flutter client filled that screen with three hard-coded prompts
("Summarise today's unread mail", "Explain this stack trace", "What's the
weather at the cabin?"). Those are a fiction: they are not derived from what
Eve can do, they never change, and two of the three are wrong for most
members of this household. The client is removing them, which leaves the
empty canvas with nothing unless a server can answer "what might this person
say first?".

That question is answerable, and by the same REFLEX call `suggest` already
makes - the inputs are just different. What it needed was a way for a client
to *ask* without that request looking like a turn.

## Decision

A `suggestions_only` flag on `config.configurable` turns a run into an
openers run:

```
load_context --> recall --> openers --> END
```

`openers` lives in `eve/suggest.py` beside `suggest`, sharing `clean`,
`_emit` and the budget/failure discipline, and differing only in its prompt
(`prompts/openers.md`) and in what it reads. It emits the **same**
`{"suggestions": [...]}` frame on the same `custom` channel, so a client needs
one handler, not two.

Three properties make it a request rather than a turn:

- **It never reaches `eve`.** No VOICE call, no answer, no cost beyond one
  REFLEX call.
- **It appends no message.** The thread it ran against is exactly as empty
  afterwards as it was before - nothing in the transcript, nothing for the
  drawer to derive a title from, nothing for the next turn to read as history.
- **It skips `extract` and `suggest`.** There is no exchange to mine facts
  from, and `suggest` would immediately overwrite the openers with `[]`.

It still routes through `recall`, which is the point of doing this on the
server at all: profile and rules are what make an opener reflect who is
asking instead of being a canned prompt with extra steps. `recall` does no
embedding call on an empty query, so this costs the always-on lookup only.

The flag reads `config.configurable`, not run metadata, for the same reason
`eve.ui.stream.capabilities` does: LangGraph indexes metadata and rejects
non-scalars there. It is checked with `is True` and **fails closed** to a
normal turn - a flag that stops Eve answering must not be trippable by a
stray string.

The client sends this against **`POST /runs/stream`** (stateless), so an
empty canvas creates no thread row at all. Aegra implements stateless runs by
generating an ephemeral thread and deleting it on completion
(`aegra_api/api/stateless_runs.py`); LangGraph Platform supports the same
endpoint. Had this used a threaded run, every app launch would have left an
empty thread for `listSessions` to filter out.

`EVE_SUGGEST_ENABLED` gates both flavours. One switch, because a deployment
that turned chips off must not have them reappear on a different route.

## Consequences

An unauthenticated stranger cannot reach this: the flag is read after
`load_context`, which resolves a member from the authenticated principal, so
an openers run is scoped to a real family member exactly like any other run.

The prompt-injection surface is *smaller* than `suggest`'s, not larger: no
human text and no Eve reply are inlined, only the member's own profile and
rules. The mitigation is unchanged - a chip is text the member sees and
chooses to send.

A client that sends the flag against a deployment predating this ADR gets a
normal turn: unknown `configurable` keys are ignored, so Eve answers an empty
input. That is the reason the client's request is opt-in and off by default
rather than something it does automatically against any LangGraph server.

The failure mode stays invisible by construction, as ADR 0013 accepted:
every failure is `[]`, and the empty canvas simply shows nothing.
`eve.openers.outcome` in Langfuse - `ok` / `empty` / `budget` / `malformed` /
`error` / `skipped` / `disabled` - is the named signal, deliberately a
*separate* attribute prefix from `eve.suggest.outcome` so the two flavours
remain separable.

`build_graph` grows an `openers_fn` seam alongside `recall_fn` / `extract_fn`
/ `suggest_fn`, so `eval/replay.py` and tests can inject a no-op.
