import math

import pytest

from eve.memory import embed


class FakeEmbedder:
    """Returns 3072 non-normalised dims, which is what gemini-embedding-001
    emits before truncation."""

    def __init__(self, dims: int = 3072, scale: float = 5.0):
        self._vec = [scale] * dims

    async def aembed_query(self, text: str) -> list[float]:
        return list(self._vec)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [list(self._vec) for _ in texts]


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    monkeypatch.setattr(embed, "get_embedder", lambda: FakeEmbedder())


async def test_query_is_truncated_to_the_pinned_dimension():
    assert len(await embed.embed_query("hi")) == 1536


async def test_query_is_renormalised_after_truncation():
    """MRL truncation breaks unit norm. Cosine distance over non-normalised
    vectors returns wrong rankings with no error - this is the assertion that
    stops that being silent."""
    vec = await embed.embed_query("hi")
    assert math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0, abs_tol=1e-9)


async def test_documents_are_batched_and_all_normalised():
    vecs = await embed.embed_texts(["a", "b", "c"])
    assert len(vecs) == 3
    for vec in vecs:
        assert len(vec) == 1536
        assert math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0, abs_tol=1e-9)


async def test_a_zero_vector_raises_rather_than_dividing_by_zero(monkeypatch):
    monkeypatch.setattr(embed, "get_embedder", lambda: FakeEmbedder(scale=0.0))
    with pytest.raises(ValueError, match="zero vector"):
        await embed.embed_query("hi")


async def test_empty_input_does_not_call_the_api(monkeypatch):
    """Extraction frequently produces no new rows. A round trip to Gemini to
    embed nothing is latency and money spent on nothing."""

    class Exploding:
        async def aembed_documents(self, texts):
            raise AssertionError("called the API with no input")

    monkeypatch.setattr(embed, "get_embedder", lambda: Exploding())
    assert await embed.embed_texts([]) == []


def test_pgvector_literal_round_trips():
    assert embed.to_pgvector([0.5, -0.25]) == "[0.5,-0.25]"
