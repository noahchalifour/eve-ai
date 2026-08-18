# 3. The embedding model and dimension are pinned

**Status:** Accepted
**Date:** 2026-08-17

## Context

Changing an embedding model invalidates every stored vector. For family
memory - the one asset in this system that cannot be rebuilt - that means a
full re-embedding migration.

## Decision

`openai:text-embedding-3-small` at 1536 dimensions, declared in
`src/eve/settings.py`. One conditional, resolved once when the Phase 2
REFLEX key is provisioned: if that key is Gemini, the model becomes
`gemini-embedding-001` truncated to 1536, so the program takes on one new
vendor rather than two. That choice is made once and never revisited.

Voyage-3 was rejected despite better benchmark position: at this corpus size
recall is dominated by entity filtering and recency weighting, so a third
vendor is not justified.

## Consequences

1536 dimensions keeps the HNSW index small and queries fast. Any future
change requires a migration that re-embeds all memory, and must update this
ADR rather than silently editing settings.
