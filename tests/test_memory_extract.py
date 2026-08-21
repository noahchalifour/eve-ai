from datetime import UTC, datetime
import importlib
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

extract_mod = importlib.import_module("eve.memory.extract")
from eve.memory.types import Extraction, Memory, Operation

MEMBER_SHARED = {
    "sub": "sub-noah", "name": "Noah", "role": "adult",
    "timezone": "America/Vancouver",
    "permissions": ["memory.write_shared"],
    "local_time": "2026-08-18 09:00 PDT",
}
MEMBER_PLAIN = {**MEMBER_SHARED, "sub": "sub-kid", "permissions": []}


@pytest.fixture
def recorded(monkeypatch):
    calls = {"add": [], "supersede": [], "reinforce": [], "forget": [],
             "embed": [], "evict": []}

    async def add(**kw):
        calls["add"].append(kw)
        return f"new-{len(calls['add'])}"

    async def supersede(old, new, why):
        calls["supersede"].append((old, new, why))

    async def reinforce(mid):
        calls["reinforce"].append(mid)

    async def forget(mid):
        calls["forget"].append(mid)

    async def set_embeddings(pairs):
        calls["embed"].extend(pairs)

    async def evict_over_cap(layer, scope_kind, scope_id, cap):
        calls["evict"].append((layer, scope_kind, scope_id, cap))
        return 0

    async def embed_texts(texts):
        return [[0.0] * 1535 + [1.0] for _ in texts]

    for name, fn in [
        ("add", add), ("supersede", supersede), ("reinforce", reinforce),
        ("forget", forget), ("set_embeddings", set_embeddings),
        ("evict_over_cap", evict_over_cap),
    ]:
        monkeypatch.setattr(extract_mod, name, fn)
    monkeypatch.setattr(extract_mod, "embed_texts", embed_texts)
    return calls


async def test_add_writes_a_row_and_embeds_it(recorded):
    ops = [Operation(op="add", layer="episodic", kind="event",
                     subject="cooper", content="Cooper had his shots.")]
    await extract_mod.apply_operations(ops, MEMBER_SHARED, "t1", "r1")
    assert recorded["add"][0]["content"] == "Cooper had his shots."
    assert recorded["add"][0]["scope_id"] == "sub-noah"
    assert len(recorded["embed"]) == 1


async def test_add_normalizes_subject_before_writing(recorded):
    """Catch a subject stored differently from lowercased search tokens."""
    ops = [Operation(op="add", layer="profile", kind="fact",
                     subject="  Cooper  ", content="Cooper likes walks.")]
    await extract_mod.apply_operations(ops, MEMBER_SHARED, "t1", "r1")
    assert recorded["add"][0]["subject"] == "cooper"


async def test_only_episodic_rows_are_embedded(recorded):
    """Profile and household are injected in full and never vector searched."""
    ops = [
        Operation(op="add", layer="profile", kind="fact", content="Vegetarian."),
        Operation(op="add", layer="episodic", kind="event", content="Went out."),
    ]
    await extract_mod.apply_operations(ops, MEMBER_SHARED, "t1", "r1")
    assert len(recorded["embed"]) == 1


async def test_household_write_requires_the_permission(recorded):
    ops = [Operation(op="add", layer="household", kind="fact",
                     content="Trash goes out Sunday.")]
    await extract_mod.apply_operations(ops, MEMBER_PLAIN, "t1", "r1")
    written = recorded["add"][0]
    assert written["layer"] == "profile"
    assert written["scope_kind"] == "member"
    assert written["scope_id"] == "sub-kid"


async def test_household_write_is_allowed_with_the_permission(recorded):
    ops = [Operation(op="add", layer="household", kind="fact",
                     content="Trash goes out Sunday.")]
    await extract_mod.apply_operations(ops, MEMBER_SHARED, "t1", "r1")
    written = recorded["add"][0]
    assert written["layer"] == "household"
    assert written["scope_kind"] == "household"
    assert written["scope_id"] == ""


async def test_supersede_points_the_old_row_at_a_newly_added_one(recorded):
    ops = [
        Operation(op="add", layer="profile", kind="fact",
                  content="Kendra works Wednesdays."),
        Operation(op="supersede", target_id="old-1"),
    ]
    await extract_mod.apply_operations(ops, MEMBER_SHARED, "t1", "r1")
    assert recorded["supersede"] == [("old-1", "new-1", "contradicted")]


async def test_supersede_with_no_replacement_still_retires_the_row(recorded):
    ops = [Operation(op="supersede", target_id="old-1")]
    await extract_mod.apply_operations(ops, MEMBER_SHARED, "t1", "r1")
    assert recorded["supersede"] == [("old-1", None, "contradicted")]


async def test_operations_missing_required_fields_are_dropped(recorded):
    ops = [
        Operation(op="add", layer="profile", kind="fact", content=None),
        Operation(op="forget", target_id=None),
        Operation(op="reinforce", target_id=None),
    ]
    counts = await extract_mod.apply_operations(ops, MEMBER_SHARED, "t1", "r1")
    assert recorded["add"] == []
    assert recorded["forget"] == []
    assert recorded["reinforce"] == []
    assert counts == {}


async def test_add_with_multiple_sentences_is_rejected_at_the_write_boundary(recorded):
    """Catch a structured-model add that would turn one durable row into two facts."""
    ops = [Operation(
        op="add", layer="profile", kind="fact",
        content="Cooper likes walks. Cooper dislikes rain.",
    )]
    counts = await extract_mod.apply_operations(ops, MEMBER_SHARED, "t1", "r1")
    assert recorded["add"] == []
    assert counts == {}


async def test_eviction_runs_for_the_layers_that_are_capped(recorded):
    ops = [Operation(op="add", layer="profile", kind="fact", content="x.")]
    await extract_mod.apply_operations(ops, MEMBER_SHARED, "t1", "r1")
    assert ("profile", "member", "sub-noah", 40) in recorded["evict"]


async def test_a_model_failure_does_not_break_the_turn(monkeypatch, recorded):
    class Boom:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, _messages):
            raise RuntimeError("gemini is down")

    monkeypatch.setattr(extract_mod, "get_model", lambda _tier: Boom())
    monkeypatch.setattr(extract_mod, "overlapping", _no_overlap)
    state = {
        "messages": [HumanMessage("hi"), AIMessage("hello")],
        "member": MEMBER_SHARED,
        "memory": None,
    }
    assert await extract_mod.extract(state, {"configurable": {}}) == {}


async def test_zero_digest_cadence_does_not_break_a_completed_turn(
    monkeypatch, recorded
):
    """Digest setup runs after streaming and must not be able to fail the turn."""
    class Recording:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, _messages):
            return Extraction(operations=[])

    monkeypatch.setattr(extract_mod, "get_model", lambda _tier: Recording())
    monkeypatch.setattr(extract_mod, "overlapping", _no_overlap)
    monkeypatch.setattr(
        extract_mod,
        "get_settings",
        lambda: SimpleNamespace(memory_digest_every_n_turns=0),
    )
    state = {
        "messages": [HumanMessage("hi"), AIMessage("hello")],
        "member": MEMBER_SHARED,
        "memory": None,
    }
    assert await extract_mod.extract(state, {"configurable": {"thread_id": "t1"}}) == {}


async def _no_overlap(sub, subjects, embedding, limit=10):
    return []


async def test_extraction_asks_the_model_about_overlapping_memories(
    monkeypatch, recorded
):
    seen = {}
    now = datetime.now(UTC)

    async def overlapping(sub, subjects, embedding, limit=10):
        return [Memory(
            id="old-1", layer="profile", scope_kind="member",
            scope_id="sub-noah", kind="fact", subject="kendra",
            content="Kendra works Tuesdays", confidence=0.7, salience=0.5,
            created_at=now, last_seen_at=now,
        )]

    class Recording:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            seen["prompt"] = messages[0].content
            return Extraction(operations=[])

    monkeypatch.setattr(extract_mod, "overlapping", overlapping)
    monkeypatch.setattr(extract_mod, "get_model", lambda _tier: Recording())
    state = {
        "messages": [HumanMessage("Kendra moved to Wednesdays"),
                     AIMessage("Got it.")],
        "member": MEMBER_SHARED,
        "memory": None,
    }
    await extract_mod.extract(state, {"configurable": {"thread_id": "t1"}})
    assert "old-1" in seen["prompt"]
    assert "Kendra works Tuesdays" in seen["prompt"]
