# 10. Memory extraction is detached from the turn and joined by the next one

**Status:** Accepted
**Date:** 2026-08-28

## Context

`extract` ran as a graph node between `eve` and `END`. Its latency never
delayed a word Eve said - `eve` has already finished streaming by the time it
starts - but it did delay the turn *ending*. A LangGraph run is complete only
when the graph reaches `END`, so an in-graph extraction holds the SSE stream,
and therefore the client's "done", open for a REFLEX model call plus
embeddings plus writes, and a second REFLEX call for the digest on every sixth
turn. In `scripts/chat.py` this shows up as the `you>` prompt hanging after
Eve has visibly finished; in a UI it is a spinner that will not stop.

Aegra does not serialize runs per thread - there is no `multitask_strategy`
and no conflict rejection, and the thread's `busy` status is a field, not a
lock - so this was never a hard block on the next turn. It is a block on the
turn looking finished, which a human client pays before every message anyway.

The obvious fix, fire-and-forget, trades that latency for a correctness
problem: turn N+1's `recall` could read memory before turn N's writes land, so
"what did I just tell you" would miss the thing it was just told. That is the
same failure ADR 0002 rejected concurrent recall for, one turn later.

## Decision

Extraction runs in a background task, registered per thread in
`eve.memory.pending`. `recall` joins that thread's pending task before it
reads memory.

The wait therefore moves off the reply path and into the gap where a member is
typing their next message. Ordering is preserved exactly: turn N+1 cannot read
memory that turn N's writes have not reached. In the common case the join
costs nothing, because a human took longer to type than Gemini took to
extract.

The join is bounded by `EVE_MEMORY_EXTRACT_JOIN_BUDGET_MS` (default 5000) and
degrades the same way the embedding arm does: if the budget runs out, the turn
proceeds with slightly stale candidates rather than hanging.
`eve.recall.extract_joined` in Langfuse reports how often that happens.
`EVE_MEMORY_EXTRACT_BACKGROUND=false` puts extraction back in the graph.

The budget bounds the *wait*, not the *work*: the joined task is shielded, so
a turn giving up on it does not cancel the writes it is partway through.

## Consequences

The registry holds strong references to every task it spawns. `asyncio` keeps
only a weak one, so an unreferenced task can be garbage-collected mid-flight
and lose its writes - the single most likely way for this design to fail
silently.

A detached extraction opens its own OpenTelemetry span. The run's span has
ended by the time it runs, and OTel silently drops attributes set on an ended
span, so without this every `eve.extract.*` and `eve.authoring.*` number -
including `eve.authoring.rules_written`, the named signal for "authoring never
fires at all" - would read as permanently absent.

**Accepted loss:** an extraction in flight when the process stops is lost.
That costs one turn's facts, in a path that already tolerates total failure
(extraction catches everything and returns). Draining on shutdown would need a
lifespan hook `eve` does not own - Aegra owns the app - and is not worth
standing one up for this. Revisit if deploys become frequent enough to notice.

**The ordering guarantee is per-process.** `eve.memory.pending`'s registry is
ordinary module-global Python state, not a shared or externally visible one.
`recall`'s join can only find an extraction that was spawned in the *same*
process. A second replica of `eve`, or a rolling deploy that routes turn N+1
to a fresh process after turn N's extraction was spawned on the old one,
means `join` finds nothing pending - not because there is nothing to wait
for, but because this process never knew about it - and returns EMPTY. The
turn then reads memory that has not been written yet, for exactly one turn,
per replica transition, with nothing crashing to say so. This is accepted for
the same reason the in-flight-task loss above is accepted: eve-ai is deployed
as a single instance today (docs/architecture.md documents the equivalent
constraint for eve-ambient, "One replica only" in its own section, for the
same underlying reason - no cross-process coordination). This ADR does not
independently verify that eve-ai's deployment manifest enforces a single
replica; if it does not, this design REQUIRES that it does, and running more
than one replica silently reintroduces the eventually-consistent read this
ADR exists to rule out. `eve.recall.extract_joined` records `"empty"` for
this case exactly as it does for the ordinary "nothing was pending" case, so
a multi-replica deployment cannot be distinguished from a healthy one by that
attribute alone - only the fraction of turns where it fires unexpectedly
would hint at it.

The pool is `max_size=5` (`memory/db.py`). Detached extractions each take a
connection, so a burst of concurrent turns contends with recall for it. Not a
problem at family scale; it is the number to look at first if it becomes one.
