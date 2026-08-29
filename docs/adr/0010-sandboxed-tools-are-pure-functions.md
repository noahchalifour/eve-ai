# 10. Sandboxed tools are pure functions, and the pod is the boundary

**Status:** Accepted
**Date:** 2026-08-27

## Context

Phase 5c lets Eve author executable code. A human approves it, but approvers
are sometimes wrong, so the interesting question is not "will the gate ever
fail" — it will — but "what does a wrong approval get you."

The obvious design gives tool code a network allowlist and a scoped
credential. That makes the approval gate load-bearing: every guarantee then
rests on a human reading a diff correctly at 11pm.

## Decision

**A sandbox tool is a pure function.** No network, no filesystem beyond a
per-call tmpfs, no environment variables, no credentials, no cluster identity.
Not an allowlist — none. A tool needing a credential is an `eve-tools`
handler, written by a human in a pull request.

Enforcement is layered, and the layers are not equal:

1. **The pod** — default-deny egress `NetworkPolicy`, no ServiceAccount token,
   no secret mounts, read-only root filesystem, non-root UID. **This is the
   security boundary.**
2. **The process** — subprocess in isolated mode with an empty environment,
   a tmpfs cwd, and rlimits on CPU, address space and core dumps.
3. **The AST allowlist** — explicitly *not* a security boundary. It is an
   accident guard and a feedback mechanism: it gives Eve an actionable error
   so she can revise before bothering a human, and it makes the approver's
   read short.

Every guarantee must hold with layer 3 assumed defeated, and the tests are
written from that assumption.

## Consequences

The remaining hostile capability of a maximally-malicious approved tool is:
burn one CPU second, allocate some memory, return a wrong answer. The first
two are bounded; the third is a correctness problem, which is what the
approval gate is actually for.

The cost is a narrower capability than "Eve writes her own tools" suggests.
A sandbox tool computes over data Eve already has: parse this iCal blob,
amortise these numbers, reformat this. Eve fetches with `eve-tools` and
computes with `eve-sandbox`. That is still a real gain — she currently does
arithmetic and parsing inside a language model, badly and unverifiably.

This extends ADR 0006 by symmetry rather than amending it: `eve-tools` holds
every credential and runs only human-written code; `eve-sandbox` runs
machine-written code and holds nothing. Two services because their invariants
are exact opposites, and one service satisfying both satisfies neither.
