import asyncio
import importlib
from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from eve.memory import recall as memory_recall
from eve.memory.types import Memory

recall_mod = importlib.import_module("eve.memory.recall")

CONFIG = {"configurable": {"thread_id": "t1"}}


def _mem(mid: str, content: str, layer: str = "episodic") -> Memory:
    now = datetime.now(UTC)
    return Memory(
        id=mid, layer=layer, scope_kind="member", scope_id="sub-noah",
        kind="event", subject=None, content=content, confidence=0.7,
        salience=0.5, created_at=now, last_seen_at=now,
    )


def _state(text: str = "how is Cooper?") -> dict:
    return {
        "messages": [HumanMessage(text)],
        "member": {
            "sub": "sub-noah", "name": "Noah", "role": "adult",
            "timezone": "America/Vancouver", "permissions": [],
            "local_time": "2026-08-18 09:00 PDT",
        },
        "system_prompt": "",
    }


@pytest.fixture
def wired(monkeypatch):
    calls = {"vector": 0}

    async def always_on(sub, thread_id):
        return ([_mem("p1", "Noah is vegetarian", "profile")],
                [_mem("h1", "The dog is Cooper", "household")],
                "They talked about dinner.")

    async def lexical(sub, query, limit=20):
        return [_mem("e1", "Cooper had his shots in June")]

    async def vector(sub, embedding, limit=20):
        calls["vector"] += 1
        return [_mem("e2", "The vet is on Fifth Avenue")]

    async def embed_query(text):
        return [0.0] * 1535 + [1.0]

    monkeypatch.setattr(recall_mod, "load_always_on", always_on)
    monkeypatch.setattr(recall_mod, "search_episodic_lexical", lexical)
    monkeypatch.setattr(recall_mod, "search_episodic_vector", vector)
    monkeypatch.setattr(recall_mod, "embed_query", embed_query)
    return calls


async def test_recall_returns_all_four_layers(wired):
    bundle = (await recall_mod.recall(_state(), CONFIG))["memory"]
    assert [m.id for m in bundle["profile"]] == ["p1"]
    assert [m.id for m in bundle["household"]] == ["h1"]
    assert {m.id for m in bundle["episodic"]} == {"e1", "e2"}
    assert bundle["digest"] == "They talked about dinner."
    assert bundle["vector_used"] is True


async def test_a_slow_embedding_degrades_to_lexical_rather_than_failing(
    monkeypatch, wired
):
    """The load-bearing property of the whole design. An untested degrade
    path does not work."""

    async def slow(text):
        await asyncio.sleep(5)
        return [0.0] * 1536

    monkeypatch.setattr(recall_mod, "embed_query", slow)
    monkeypatch.setattr(
        recall_mod, "EMBED_BUDGET_OVERRIDE_S", 0.01, raising=False
    )
    bundle = (await recall_mod.recall(_state(), CONFIG))["memory"]

    assert bundle["vector_used"] is False
    assert [m.id for m in bundle["episodic"]] == ["e1"]
    assert bundle["profile"], "always-on layers must survive a degraded turn"
    assert wired["vector"] == 0


async def test_embedding_that_finishes_after_its_budget_is_not_used(
    monkeypatch, wired
):
    """Catch a deadline applied only after slow store reads complete."""
    release_embedding = asyncio.Event()

    async def embed_after_budget(text):
        await release_embedding.wait()
        return [0.0] * 1535 + [1.0]

    async def slow_always_on(sub, thread_id):
        await asyncio.sleep(0.03)
        release_embedding.set()
        await asyncio.sleep(0)
        return ([_mem("p1", "Noah is vegetarian", "profile")],
                [_mem("h1", "The dog is Cooper", "household")],
                "They talked about dinner.")

    monkeypatch.setattr(recall_mod, "embed_query", embed_after_budget)
    monkeypatch.setattr(recall_mod, "load_always_on", slow_always_on)
    monkeypatch.setattr(recall_mod, "EMBED_BUDGET_OVERRIDE_S", 0.01)

    bundle = (await recall_mod.recall(_state(), CONFIG))["memory"]

    assert bundle["vector_used"] is False
    assert [m.id for m in bundle["episodic"]] == ["e1"]
    assert wired["vector"] == 0


async def test_cancelling_recall_cancels_and_awaits_its_embedding(
    monkeypatch, wired
):
    """Catch an embedding task detached when recall is cancelled mid-read."""
    embedding_started = asyncio.Event()
    embedding_cancelled = asyncio.Event()
    release_embedding = asyncio.Event()
    store_started = asyncio.Event()

    async def blocking_embed(text):
        embedding_started.set()
        try:
            await release_embedding.wait()
            return [0.0] * 1535 + [1.0]
        finally:
            embedding_cancelled.set()

    async def blocking_always_on(sub, thread_id):
        store_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(recall_mod, "embed_query", blocking_embed)
    monkeypatch.setattr(recall_mod, "load_always_on", blocking_always_on)

    task = asyncio.create_task(recall_mod.recall(_state(), CONFIG))
    try:
        await embedding_started.wait()
        await store_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(embedding_cancelled.wait(), timeout=0.1)
    finally:
        release_embedding.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_package_recall_export_is_callable():
    assert callable(memory_recall)


async def test_a_failing_embedding_degrades_rather_than_raising(
    monkeypatch, wired
):
    async def boom(text):
        raise RuntimeError("gemini is down")

    monkeypatch.setattr(recall_mod, "embed_query", boom)
    bundle = (await recall_mod.recall(_state(), CONFIG))["memory"]
    assert bundle["vector_used"] is False
    assert [m.id for m in bundle["episodic"]] == ["e1"]


async def test_no_human_message_skips_retrieval_but_keeps_always_on(wired):
    """A resumed run can reach recall with no new human turn. Embedding an
    empty string is a wasted call, but the standing facts still belong in
    the prompt."""
    state = _state()
    state["messages"] = [AIMessage("hello")]
    bundle = (await recall_mod.recall(state, CONFIG))["memory"]
    assert bundle["episodic"] == []
    assert bundle["profile"]
    assert bundle["vector_used"] is False


async def test_the_budget_truncates_episodic_and_reports_the_count(
    monkeypatch, wired
):
    async def many(sub, query, limit=20):
        return [_mem(f"e{i}", "x" * 400) for i in range(20)]

    monkeypatch.setattr(recall_mod, "search_episodic_lexical", many)
    bundle = (await recall_mod.recall(_state(), CONFIG))["memory"]
    assert 0 < len(bundle["episodic"]) < 20
