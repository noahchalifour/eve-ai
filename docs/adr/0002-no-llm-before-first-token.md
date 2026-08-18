# 2. No model call may precede the first streamed token

**Status:** Accepted
**Date:** 2026-08-17

## Context

Eve must feel like talking to a person. The standard ways to erode that are
a router model that classifies intent before answering, and a memory lookup
that blocks the response.

## Decision

Nothing that requires a model call sits in front of Eve. `load_context` is
pure local computation. Anything that would logically precede her runs
CONCURRENTLY with her first tokens and merges into a later turn or a
mid-stream update.

Target: p50 first token under 1s, p95 under 2s, measured from request receipt
to the first SSE content event.

## Consequences

Phase 2's memory recall runs in parallel with the model call rather than
ahead of it, which constrains how recall results may be used. This is the
constraint most likely to be violated by a well-meaning later change, which
is why it is recorded rather than left implicit.
