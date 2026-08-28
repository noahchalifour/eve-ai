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

    async def always_on(sub, thread_id, *, include_rules=False):
        return ([_mem("p1", "Noah is vegetarian", "profile")],
                [_mem("h1", "The dog is Cooper", "household")],
                "They talked about dinner.",
                [])

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

    async def slow_always_on(sub, thread_id, *, include_rules=False):
        await asyncio.sleep(0.03)
        release_embedding.set()
        await asyncio.sleep(0)
        return ([_mem("p1", "Noah is vegetarian", "profile")],
                [_mem("h1", "The dog is Cooper", "household")],
                "They talked about dinner.",
                [])

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

    async def blocking_always_on(sub, thread_id, *, include_rules=False):
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


async def test_recall_loads_rules_when_authoring_is_enabled(monkeypatch):
    from datetime import UTC, datetime
    from eve.settings import get_settings

    now = datetime(2026, 8, 27, tzinfo=UTC)
    rule = Memory(
        id="r1", layer="rule", scope_kind="member", scope_id="sub-noah",
        kind="preference", subject=None, content="Lead with the number.",
        confidence=0.8, salience=0.6, created_at=now, last_seen_at=now,
    )
    seen = {}

    async def load_always_on(sub, thread_id, *, include_rules=False):
        seen["include_rules"] = include_rules
        return [], [], None, ([rule] if include_rules else [])

    async def search_episodic_lexical(sub, query, limit=20):
        return []

    monkeypatch.setattr(recall_mod, "load_always_on", load_always_on)
    monkeypatch.setattr(recall_mod, "search_episodic_lexical", search_episodic_lexical)
    monkeypatch.setenv("EVE_SELF_AUTHORING_ENABLED", "true")

    get_settings.cache_clear()

    state = {"member": {"sub": "sub-noah"}, "messages": []}
    out = await recall_mod.recall(state, {"configurable": {"thread_id": "t1"}})

    assert seen["include_rules"] is True
    assert [m.content for m in out["memory"]["rules"]] == ["Lead with the number."]


async def test_recall_bundle_always_has_a_rules_key(monkeypatch):
    """Disabled must still produce a well-formed bundle: build_system_prompt
    and every consumer read the key unconditionally."""
    from eve.settings import get_settings

    async def load_always_on(sub, thread_id, *, include_rules=False):
        return [], [], None, []

    async def search_episodic_lexical(sub, query, limit=20):
        return []

    monkeypatch.setattr(recall_mod, "load_always_on", load_always_on)
    monkeypatch.setattr(recall_mod, "search_episodic_lexical", search_episodic_lexical)
    monkeypatch.setenv("EVE_SELF_AUTHORING_ENABLED", "false")

    get_settings.cache_clear()

    out = await recall_mod.recall(
        {"member": {"sub": "sub-noah"}, "messages": []}, {"configurable": {}}
    )
    assert out["memory"]["rules"] == []


def _budget_rows(layer: str) -> list[Memory]:
    """Five equal 100-token rows, so a 1200-token budget split three ways
    (400) keeps four of them and split four ways (300) keeps three."""
    return [_mem(f"{layer}{i}", "x" * 400, layer) for i in range(5)]


@pytest.mark.parametrize(
    ("enabled", "divisor"), [("false", 3), ("true", 4)]
)
async def test_the_always_on_share_is_only_split_four_ways_when_authoring_is_on(
    monkeypatch, enabled, divisor
):
    """EVE_SELF_AUTHORING_ENABLED=false must behave EXACTLY like Phase 4.

    An unconditional `// 4` shrinks every deployment's profile/household
    prompt share by a quarter to make room for a rule layer that is
    guaranteed empty when the setting is off - a silent behaviour change in
    deployments that never opted into this feature.
    """
    from eve.memory.ranking import fit_budget
    from eve.settings import get_settings

    profile_rows = _budget_rows("profile")
    household_rows = _budget_rows("household")

    async def load_always_on(sub, thread_id, *, include_rules=False):
        return list(profile_rows), list(household_rows), None, []

    async def search_episodic_lexical(sub, query, limit=20):
        return []

    monkeypatch.setattr(recall_mod, "load_always_on", load_always_on)
    monkeypatch.setattr(recall_mod, "search_episodic_lexical", search_episodic_lexical)
    monkeypatch.setenv("EVE_SELF_AUTHORING_ENABLED", enabled)

    get_settings.cache_clear()
    share = get_settings().memory_token_budget // divisor

    out = await recall_mod.recall(
        {"member": {"sub": "sub-noah"}, "messages": []}, {"configurable": {}}
    )
    bundle = out["memory"]

    assert [m.id for m in bundle["profile"]] == [
        m.id for m in fit_budget(profile_rows, share)
    ]
    assert [m.id for m in bundle["household"]] == [
        m.id for m in fit_budget(household_rows, share)
    ]
    # The two divisors must actually differ here, or the assertions above
    # would pass against either one and prove nothing.
    assert len(bundle["profile"]) == {3: 4, 4: 3}[divisor]
