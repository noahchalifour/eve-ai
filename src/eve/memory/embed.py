"""The embedding client.

Truncate to the pinned dimension, then RE-NORMALISE. gemini-embedding-001
emits 3072 dimensions trained with Matryoshka representation learning, and a
truncated MRL vector is no longer unit-norm. pgvector's cosine operator does
not care and does not complain; it just ranks wrong. See ADR 0003.

Truncation is unconditional even though LiteLLM may honour `dimensions` and
return 1536 already - in which case the slice is a no-op and the cost is one
comparison. Depending on the proxy to do it is depending on a remote config
we do not own.
"""

from __future__ import annotations

import math
from functools import lru_cache

from langchain_openai import OpenAIEmbeddings

from eve.settings import get_settings


@lru_cache(maxsize=1)
def get_embedder() -> OpenAIEmbeddings:
    settings = get_settings()
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        dimensions=settings.embedding_dims,
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key or "unset",
        # LiteLLM proxies a non-OpenAI model here. langchain's context-length
        # check runs tiktoken against an OpenAI tokeniser that does not
        # describe Gemini, and it rewrites the request body when it trips.
        check_embedding_ctx_length=False,
    )


def _normalise(vec: list[float], dims: int) -> list[float]:
    vec = vec[:dims]
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        raise ValueError("embedding is the zero vector; refusing to normalise")
    return [v / norm for v in vec]


async def embed_query(text: str) -> list[float]:
    dims = get_settings().embedding_dims
    return _normalise(await get_embedder().aembed_query(text), dims)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    dims = get_settings().embedding_dims
    raw = await get_embedder().aembed_documents(texts)
    return [_normalise(vec, dims) for vec in raw]


def to_pgvector(vec: list[float]) -> str:
    """pgvector's text input format.

    ponytail: a string literal cast with `%s::vector` rather than the
    `pgvector` package's psycopg adapter. One function against a stable wire
    format, versus a dependency and a per-connection registration hook.
    """
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


def from_pgvector(raw: str) -> list[float]:
    """The inverse of `to_pgvector`. Without the adapter this module avoids
    registering, psycopg has no type mapping for `vector` and returns the
    column as this same text literal (`"[0.1,0.2,...]"`) rather than a
    Python list - confirmed against the real column, not assumed."""
    return [float(v) for v in raw.strip("[]").split(",")]
