# 19. One generic record store, and widgets are recipes over it

**Status:** Accepted
**Date:** 2026-09-14
**Relates to:** [ADR 0008](0008-authored-behaviour-is-memory.md), [ADR 0016](0016-eve-tools-owns-a-credential-table.md)

## Context

Reusable widgets need somewhere to read from. The obvious design gives each
domain its own table: workout sets here, reading sessions there, chores
somewhere else. That makes every new thing a member wants to track a
migration, a store module, a tool, and a release, which is precisely the
per-domain cost widgets exist to remove.

ADR 0008 already refused this shape once, storing authored rules and
procedures as `eve_memory` layers rather than building a store per kind.

## Decision

**One table, `eve_record`**: member, a free-form `collection` name, an opaque
jsonb `payload`, and `occurred_at`. A workout set, a book finished and a chore
done are the same row with a different collection string. Two tools,
`record_append` and `record_query`, are the only writers and readers; what a
domain MEANS lives in a skill, which is prose.

**A widget is a recipe over that store**, not code. `eve_widget_resource`
holds a validated declarative recipe naming allowlisted sources; the snapshot
route executes it with no model and no graph run. Adding a widget costs a
recipe. Adding an external SYSTEM still costs an audited reader, because
credentials and normalisation cannot be authored by a model.

Not `eve_memory`, despite the precedent: memory is prose with embeddings,
decay and salience, and reconstructing a numeric series over ninety days from
embedded sentences is lexical guesswork rather than aggregation. Not the
Aegra Store either: it is genuinely identity-namespaced but answers key
lookups, so every range query would fetch and filter in application code.
`occurred_at` as a real indexed column is the whole difference.

## Consequences

A new tracked domain is a collection name and a skill paragraph. No DDL, no
deploy, no client release.

The cost is that collection names are unconstrained, so a model can invent a
near-duplicate (`workout.set` versus `workouts`) and split a member's history
in two. `record_append` returns the collection's previously-seen field names
to steer consistency, and the skill tells the model to query before inventing
a name, but neither is enforcement: a divergent write is stored and visible
rather than rejected, because member-recorded data must never be lost to a
schema disagreement it cannot see.

The recipe validator (`eve.widgets.recipe`) becomes a security boundary of
the same weight as the sandbox AST check was NOT (ADR 0010): unlike that
checker, this one IS load-bearing, because a recipe executes forever after
with no human and no model in the loop. Its vocabulary is closed and its
tests assume a hostile author.
