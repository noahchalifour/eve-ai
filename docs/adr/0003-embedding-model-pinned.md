# 3. The embedding model and dimension are pinned

**Status:** Accepted
**Date:** 2026-08-17

## Context

Changing an embedding model invalidates every stored vector. For family
memory - the one asset in this system that cannot be rebuilt - that means a
full re-embedding migration.

## Decision

**Amended 2026-08-18.** The conditional below resolved when the metered
REFLEX key was provisioned. The key is Gemini, so the embedding model is
`gemini/gemini-embedding-001`, truncated to 1536 dimensions and
**re-normalised to unit length**, declared in `src/eve/settings.py`.

The original Phase 1 decision was `openai:text-embedding-3-small` at 1536
dimensions, with one conditional: if the REFLEX key turned out to be Gemini,
the model became `gemini-embedding-001` truncated to 1536, so the program
took on one new vendor rather than two. That is what happened. The
conditional is now spent and this ADR carries no open questions.

Voyage-3 was rejected despite better benchmark position: at this corpus size
recall is dominated by entity filtering and recency weighting, so a third
vendor is not justified.

### Re-normalisation is not optional

`gemini-embedding-001` emits 3072 dimensions trained with Matryoshka
representation learning. A truncated MRL vector is no longer unit-norm, and
cosine similarity over non-normalised vectors returns wrong rankings with no
error and no crash - just quietly worse recall that nobody attributes to
this. `src/eve/memory/embed.py` re-normalises unconditionally, and
`tests/test_live_embeddings.py` pins the proxy's actual behaviour.

## Consequences

1536 dimensions keeps the HNSW index small and queries fast. Any future
change requires a migration that re-embeds all memory, and must update this
ADR rather than silently editing settings.
