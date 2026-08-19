# 2. No model call may precede the first streamed token

**Status:** Accepted
**Date:** 2026-08-17

## Context

Eve must feel like talking to a person. The standard ways to erode that are
a router model that classifies intent before answering, and a memory lookup
that blocks the response.

## Decision

**Amended 2026-08-18 (Phase 2).** No *generative* model call sits in front of
Eve. `load_context` remains pure local computation. `recall` may make exactly
one embedding call, which must be bounded by
`EVE_MEMORY_RECALL_EMBED_BUDGET_MS` (default 120) and cancellable, and whose
absence must leave a complete turn.

Target, unchanged: p50 first token under 1s, p95 under 2s, measured from
request receipt to the first SSE content event.

### What was withdrawn, and why

The original decision said memory recall "runs CONCURRENTLY with her first
tokens and merges into a later turn or a mid-stream update." That is
withdrawn. Concurrent recall cannot inform the answer it runs alongside, so
"what did we decide about the kitchen?" would miss on the turn it was asked
and land on the next one - which is worse than no episodic memory at all,
because it looks like Eve is ignoring the question.

The enemy this ADR was written against was a router model classifying intent
before answering: hundreds of milliseconds of generative latency, unbounded,
on the critical path. An embedding call is a different animal - about 100ms,
bounded, and cancellable. Admitting it is a smaller concession than the
original wording's alternative.

## Consequences

`src/eve/memory/recall.py` runs the lexical arm immediately and races the
embedding against the budget. If the embedding misses, the turn ships with
profile, household, digest and lexically-ranked episodic memory intact - the
degrade costs paraphrase matching for one turn and nothing else.
`eve.recall.vector_used` in Langfuse reports how often that happens; if it is
often, the honest response is to drop the vector arm, not to raise the budget.

This remains the constraint most likely to be violated by a well-meaning later
change. Phase 3's tools loop in particular must not put a model call between
the request and the first token.
