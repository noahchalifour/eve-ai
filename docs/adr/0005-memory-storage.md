# 5. Memory storage: one table, supersession, read-time decay

**Status:** Accepted
**Date:** 2026-08-18

## Context

Phase 2 stores four memory layers - profile, household, episodic, digest -
with contradiction handling and decay, for a family of five. Aegra already
provides a vector-capable `AsyncPostgresStore` injected into every node, which
was the obvious candidate.

## Decision

**One table, `eve_memory`, owned by Eve.** Layers are a column. They differ in
retrieval policy, not in shape; four tables would mean four queries, four
migrations, and four places to fix the same bug.

**Not Aegra's store.** Its `search` has no full-text arm and no way to express
recency weighting, so hybrid recall is not expressible against it.
Supersession, confidence and decay would live in JSON, making every
contradiction check an over-fetch-then-filter-in-Python instead of an indexed
predicate.

**Supersession, not deletion.** Retirement always sets `superseded_why` and
sets `superseded_by` only when a replacement exists. The live predicate is
`superseded_why IS NULL`; partial indexes and every read use it. An eviction
has `superseded_why='evicted'` and `superseded_by=NULL`, so
`superseded_by IS NULL` would incorrectly treat it as live. Phase 5's eval
harness needs to answer "what did Eve believe on the day she got that wrong."

The one exception is an explicit instruction to forget, which hard-deletes.
A tombstone that still holds the text is not forgetting, and treating it as
such would be a quiet lie to a family member about their own data.

**Read-time decay, no scheduled jobs.** Decay is
`exp(-ln(2) * age / half_life)` evaluated in the query, so the score is exactly
one half at one half-life. A nightly-refreshed `decayed_score` column would be
a cache of an expression cheaper to evaluate than to maintain, and wrong for
as long as the pod was down. Eviction and contradiction resolution happen in
the `extract` node instead of a cron, because the turn that reveals a
contradiction is the only place the context to resolve it exists.

## Consequences

Eve owns a schema for the first time, applied by a hand-rolled ordered-DDL
runner under a Postgres advisory lock. The `eve-migrate` console script runs
before Aegra via the Dockerfile's
`CMD ["sh", "-c", "eve-migrate && exec aegra serve"]`, so a migration failure
stops the container before the server starts. This is not Alembic because
there is one table; move to Alembic past roughly five migrations.

Phase 2 introduces no cron, no worker, and no scheduled job of any kind.
