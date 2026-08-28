import pytest


async def test_replay_ambient_calls_the_real_judge(monkeypatch):
    from eve.eval import replay as replay_mod
    from eve.eval.types import DatasetItem
    from eve_ambient.types import FilterVerdict

    seen = {}

    async def judge(signal):
        seen["summary"] = signal.summary
        return FilterVerdict(notify=True, audience=["sub-noah"], why="w")

    monkeypatch.setattr(replay_mod, "judge", judge)

    item = DatasetItem(
        id="d1", shape="ambient",
        input={"signal": {
            "source": "mail", "key": "k1",
            "occurred_at": "2026-08-27T00:00:00+00:00",
            "member_sub": "sub-noah", "summary": "A package shipped.",
            "payload": {}, "cooldown_hours": None,
        }},
        expected={"notify": True, "audience": ["sub-noah"], "urgent": False},
    )
    out = await replay_mod.replay_ambient(item)

    assert seen["summary"] == "A package shipped."
    assert out["notify"] is True


async def test_replay_ambient_reports_a_filter_error_rather_than_raising(monkeypatch):
    from eve.eval import replay as replay_mod
    from eve.eval.types import DatasetItem
    from eve_ambient.filter import FilterError

    async def judge(signal):
        raise FilterError("could not decide")

    monkeypatch.setattr(replay_mod, "judge", judge)

    item = DatasetItem(
        id="d1", shape="ambient",
        input={"signal": {
            "source": "mail", "key": "k1",
            "occurred_at": "2026-08-27T00:00:00+00:00",
            "member_sub": None, "summary": "x", "payload": {},
            "cooldown_hours": None,
        }},
        expected={},
    )
    out = await replay_mod.replay_ambient(item)
    assert out["error"] is True


async def test_replay_turn_never_runs_extract(monkeypatch):
    """An eval run that writes memory corrupts the thing it is measuring."""
    import sys

    from eve.eval import replay as replay_mod
    from eve.family import Family, Member

    called = []

    async def real_extract(state, config):
        called.append(1)
        return {}

    # `eve.memory`'s __init__ does `from eve.memory.extract import extract`,
    # which rebinds the `extract` attribute on the `eve.memory` package to the
    # function itself - so the dotted string "eve.memory.extract.extract"
    # resolves through `eve.memory.extract` (the now-shadowed function) rather
    # than the submodule, and monkeypatch.setattr's string form can't reach
    # the real submodule that way. Go through sys.modules to patch the
    # submodule object directly, which importing eve.graph (transitively, via
    # replay_mod) has already registered there.
    monkeypatch.setattr(sys.modules["eve.memory.extract"], "extract", real_extract)
    monkeypatch.setattr(replay_mod, "_model_factory", _fake_factory("Hello."))

    # `replay_turn` builds the graph with only `extract_fn` overridden - the
    # module docstring's "the one substitution" - so `recall_fn` keeps its
    # `build_graph` default, `eve.memory.recall.recall`, which is bound once
    # at `eve.graph` import time and so cannot be swapped out from here.
    # Faking the DB calls it makes (the way `search_episodic_lexical`'s own
    # docstring says that arm "CANNOT FAIL") keeps this a unit test: real
    # recall behaviour with a real Postgres is covered by the marked
    # `integration` tests, not this one. `item.input["message"]` is non-empty,
    # so `recall()` also starts an embedding task (its vector arm) - leaving
    # `embed_query`/`search_episodic_vector` real would make the test's
    # determinism depend on an unmocked call to the real LiteLLM proxy
    # failing fast rather than, in an environment with valid credentials,
    # actually succeeding and then hitting the same missing-database error
    # through the unmocked vector search. Faking all four matches
    # tests/test_memory_recall.py's `wired` fixture.
    async def fake_load_always_on(sub, thread_id, *, include_rules=False):
        return [], [], None, []

    async def fake_search_episodic_lexical(sub, query, limit=20):
        return []

    async def fake_search_episodic_vector(sub, embedding, limit=20):
        return []

    async def fake_embed_query(text):
        return [0.0]

    # Same shadowing as `eve.memory.extract` above: `eve/memory/__init__.py`
    # does `from eve.memory.recall import recall`, so `eve.memory.recall` (the
    # attribute) is the `recall` function, not the submodule. sys.modules
    # still holds the real submodule under its full dotted name.
    recall_module = sys.modules["eve.memory.recall"]
    monkeypatch.setattr(recall_module, "load_always_on", fake_load_always_on)
    monkeypatch.setattr(
        recall_module, "search_episodic_lexical", fake_search_episodic_lexical
    )
    monkeypatch.setattr(
        recall_module, "search_episodic_vector", fake_search_episodic_vector
    )
    monkeypatch.setattr(recall_module, "embed_query", fake_embed_query)

    monkeypatch.setattr(
        "eve.context.get_family",
        lambda: Family(
            [
                Member(
                    sub="sub-noah",
                    name="Noah",
                    role="adult",
                    timezone="America/Toronto",
                    permissions=frozenset({"spend"}),
                )
            ]
        ),
    )
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    from eve.eval.types import DatasetItem

    item = DatasetItem(
        id="t1", shape="turns",
        input={"member": "sub-noah", "message": "Say hello."},
        expected={"expects": ["It greets."]},
    )
    outcome = await replay_mod.replay_turn(item, suppress_rules=False)

    assert outcome["error"] is False
    assert "Hello." in outcome["text"]
    assert called == []


async def test_replay_turn_reports_an_error_rather_than_raising(monkeypatch):
    """Mirrors replay_ambient's posture: an unknown member sub (or any other
    graph failure) must not abort a run of many items."""
    from eve.eval import replay as replay_mod
    from eve.eval.types import DatasetItem

    def boom(*args, **kwargs):
        raise RuntimeError("graph blew up")

    monkeypatch.setattr(replay_mod, "build_graph", boom)

    item = DatasetItem(
        id="t1", shape="turns",
        input={"member": "sub-does-not-exist", "message": "hi"},
        expected={"expects": []},
    )
    outcome = await replay_mod.replay_turn(item, suppress_rules=False)

    assert outcome == {"text": "", "error": True}


def test_voice_call_estimate_counts_both_arms():
    from eve.eval.replay import voice_call_estimate
    from eve.eval.types import DatasetItem

    items = [
        DatasetItem(id=str(n), shape="turns", input={}, expected={})
        for n in range(5)
    ]
    assert voice_call_estimate(items, arms=2) == 10
    assert voice_call_estimate(items, arms=1) == 5


def _fake_factory(reply: str):
    from langchain_core.messages import AIMessage

    from tests.conftest import FakeToolCallingModel

    def factory(tier):
        return FakeToolCallingModel(messages=iter([AIMessage(reply)] * 50))

    return factory
