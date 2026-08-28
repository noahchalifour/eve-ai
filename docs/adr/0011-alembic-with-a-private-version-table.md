# 11. Eve's migrations use Alembic with a private version table

**Status:** Accepted
**Date:** 2026-08-27

## Context

Phase 2 hand-rolled migrations as an ordered list in `db.py`, for two stated
reasons: Aegra already runs its own Alembic migrations at startup and ours
must not interleave with them, and there were only four tables. The module's
own comment set the review trigger: "Move to Alembic if MIGRATIONS exceeds ~5
entries."

Phase 5b brought it to exactly 5. `eve_tool` is the sixth.

## Decision

Eve's schema moves to Alembic with its own `script_location` and, critically,
`version_table="eve_alembic_version"`. Revision one (`0001_baseline`)
reproduces the five hand-rolled entries idempotently — every statement is
`IF NOT EXISTS` — so it is a no-op against an already-migrated database and a
full create against a fresh one. Revision two (`0002_eve_tool`) adds
`eve_tool`.

A third revision, `0003_eve_tool_pending_dedup`, was added during this
branch's own review process, not planned up front. `store.propose()`'s
dedup guard against LangGraph's interrupt-replay (existence check, then
insert) runs with no `SELECT ... FOR UPDATE`, so two genuinely concurrent
proposals could both pass the check before either commits and produce a
duplicate pending row. The approved case already had a real backstop — the
partial unique index `eve_tool_live_name` from revision two — so revision
three gives the pending case the same shape of guarantee: a second partial
unique index, `eve_tool_pending_dedup`, over `(name, source_sha256,
source_thread)` where the row is still undecided. Three revisions land in
this phase, not two; the third exists because review is part of how this
schema got correct, not because the plan under-scoped it.

`eve-migrate` keeps its name and its contract: run before `aegra serve`, fail
the pod loudly on a schema problem. It now shells out to `alembic upgrade
head` under the same advisory lock the ordered list used, so two pods starting
at once still cannot race.

`eve_schema_version` is left in place and unused. Dropping it would make a
rollback to the previous image fail on a table it expects.

## Consequences

The original constraint is preserved, not abandoned: two independent migration
histories against one database, each with its own version table, cannot stamp
over each other. What changes is that Eve gains ordering, autogeneration, and
downgrades for the schema changes past this point.

The risk this introduces is baseline drift — revision one must reproduce every
object the five entries created, or a fresh deployment differs from an upgraded
one. The plan includes an object-set diff against `db.MIGRATIONS` for exactly
that reason, and the integration test asserts every Phase 1–5b table exists
after a migrate from empty.
