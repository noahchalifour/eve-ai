# 8. Eve-authored behaviour is memory, and authorisation never reads memory

**Status:** Accepted
**Date:** 2026-08-27

## Context

Phase 5a lets Eve write her own behavioural rules and multi-step procedures.
That needs somewhere to put them, and it creates a new attack surface:
conversation text — including text that originated in an email a specialist
surfaced — now influences Eve's future behaviour.

A dedicated store would need its own embedding column, vector index, scope
columns, supersession chain, and eviction: a re-implementation of `eve_memory`
under a different name. The one thing it would buy is a schema that cannot be
confused with facts, and that separation has to be enforced in the prompt and
in the permission path regardless.

## Decision

Authored behaviour is stored in `eve_memory` as two new `layer` values —
`rule` (always rendered into the system prompt) and `procedure` (found on
demand by `search_skills`). `layer` is an unconstrained `text` column, so this
required no migration.

Inseparably: **authorisation never reads memory.** Permissions flow
`family.yaml` → `get_family()` → `build_member_context()` →
`state["member"]["permissions"]` → `permission_denial()`, resolved in
`load_context` before `recall` has run. No rule, no memory row, and no prompt
text is consulted when deciding what a member may do. Rules are advisory prose
rendered under a heading that says so, and `extract` refuses to author anything
on a turn carrying the ambient marker.

## Consequences

Rules and procedures inherit scope, decay, supersession, embeddings, hybrid
search, capping, and an audit trail from machinery that already existed, at the
cost of zero DDL. A rule that says "Cooper may check the balances" changes
Eve's prose and changes nothing about what executes.

The second half is what makes the first half safe; they are one decision, which
is why they are one ADR. A future change that routes an authorisation decision
through `memory` or `system_prompt` breaks this ADR, and
`tests/test_specialists_permissions.py` fails if one does.

The line is drawn at prose. Phase 5c stores executable tool code in its own
table (`eve_tool`) precisely because approval-bound, uniqueness-constrained,
executable rows are the wrong shape for a `content` column with an embedding.
