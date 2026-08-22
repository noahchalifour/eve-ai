"""tests/test_memory_search.py"""
from datetime import UTC, datetime

from eve.memory.search import search_memory
from eve.memory.types import Memory

NOW = datetime.now(UTC)


def _memory(id_, content):
    return Memory(
        id=id_, layer="episodic", scope_kind="member", scope_id="sub-noah",
        kind="event", subject=None, content=content, confidence=0.7,
        salience=0.5, created_at=NOW, last_seen_at=NOW,
    )


def _state():
    return {
        "messages": [],
        "member": {
            "sub": "sub-noah",
            "name": "Noah",
            "role": "user",
            "timezone": "America/Los_Angeles",
            "permissions": [],
            "local_time": "2024-01-01T12:00:00",
        },
        "system_prompt": "You are Eve, a family AI assistant.",
        "memory": None,
        "dynamic_tools": [],
    }


async def test_search_memory_merges_lexical_and_vector_results(monkeypatch):
    async def _lexical(sub, query, limit=10):
        return [_memory("1", "Decided to replace the dishwasher.")]

    async def _vector(sub, embedding, limit=10):
        return [_memory("2", "The kitchen needs a new dishwasher.")]

    async def _embed(q):
        return [0.1, 0.2]

    monkeypatch.setattr("eve.memory.search.search_episodic_lexical", _lexical)
    monkeypatch.setattr("eve.memory.search.search_episodic_vector", _vector)
    monkeypatch.setattr("eve.memory.search.embed_query", _embed)
    state = _state()
    result = await search_memory.ainvoke({"query": "dishwasher", "state": state})
    assert "Decided to replace the dishwasher." in result
    assert "The kitchen needs a new dishwasher." in result


async def test_search_memory_degrades_to_lexical_only_on_embedding_failure(monkeypatch):
    async def _lexical(sub, query, limit=10):
        return [_memory("1", "Cooper's vet appointment is Tuesday.")]

    async def _fail(_query):
        raise RuntimeError("embedding service down")

    monkeypatch.setattr("eve.memory.search.search_episodic_lexical", _lexical)
    monkeypatch.setattr("eve.memory.search.embed_query", _fail)
    state = _state()
    result = await search_memory.ainvoke({"query": "vet", "state": state})
    assert "Cooper's vet appointment is Tuesday." in result


async def test_search_memory_reports_nothing_found(monkeypatch):
    async def _empty(*a, **k):
        return []

    async def _embed(q):
        return [0.0]

    monkeypatch.setattr("eve.memory.search.search_episodic_lexical", _empty)
    monkeypatch.setattr("eve.memory.search.search_episodic_vector", _empty)
    monkeypatch.setattr("eve.memory.search.embed_query", _embed)
    state = _state()
    result = await search_memory.ainvoke({"query": "nonexistent", "state": state})
    assert result == "Nothing found."
