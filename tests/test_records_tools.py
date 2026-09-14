"""The two generic record tools. No test here names a domain: if one did,
the tools would have learned something they must not know."""
from __future__ import annotations

import pytest

CONFIG = {"configurable": {"member": {"sub": "sub-noah"}}}


def _call(tool, args):
    return tool.ainvoke(
        {"type": "tool_call", "name": tool.name, "args": args, "id": "t1"},
        config=CONFIG,
    )


async def test_append_writes_for_the_authenticated_member(monkeypatch):
    from eve.records import tools

    seen = {}

    async def fake_append(member_sub, collection, payload, occurred_at=None, key=None):
        seen.update(
            member_sub=member_sub, collection=collection,
            payload=payload, key=key,
        )
        return {"id": "r1", "deduped": False}

    async def fake_known_fields(member_sub, collection):
        return ["weight"]

    monkeypatch.setattr(tools.store, "append", fake_append)
    monkeypatch.setattr(tools.store, "known_fields", fake_known_fields)

    result = await _call(
        tools.record_append,
        {"collection": "alpha.thing", "payload": {"weight": 100}},
    )

    assert seen["member_sub"] == "sub-noah"
    assert seen["collection"] == "alpha.thing"
    assert "weight" in result.content


async def test_append_ignores_a_model_supplied_member(monkeypatch):
    """Identity is the authenticated principal's, never an argument."""
    from eve.records import tools

    seen = {}

    async def fake_append(member_sub, collection, payload, occurred_at=None, key=None):
        seen["member_sub"] = member_sub
        return {"id": "r1", "deduped": False}

    monkeypatch.setattr(tools.store, "append", fake_append)
    monkeypatch.setattr(tools.store, "known_fields", lambda *a: _none())

    result = await _call(
        tools.record_append,
        {
            "collection": "alpha.thing",
            "payload": {"member_sub": "sub-kendra", "weight": 100},
        },
    )

    assert seen["member_sub"] == "sub-noah"
    assert "sub-kendra" not in str(seen.get("member_sub"))


async def _none():
    return []


async def test_append_reports_the_collections_known_fields(monkeypatch):
    """The learned shape steers the next write; it never rejects this one."""
    from eve.records import tools

    async def fake_append(member_sub, collection, payload, occurred_at=None, key=None):
        return {"id": "r1", "deduped": False}

    async def fake_known_fields(member_sub, collection):
        return ["duration", "label"]

    monkeypatch.setattr(tools.store, "append", fake_append)
    monkeypatch.setattr(tools.store, "known_fields", fake_known_fields)

    result = await _call(
        tools.record_append,
        {"collection": "alpha.thing", "payload": {"totally": "different"}},
    )

    assert "duration" in result.content
    assert "label" in result.content


async def test_a_divergent_payload_is_still_stored(monkeypatch):
    """Member data is never lost to a shape disagreement."""
    from eve.records import tools

    stored = []

    async def fake_append(member_sub, collection, payload, occurred_at=None, key=None):
        stored.append(payload)
        return {"id": "r1", "deduped": False}

    monkeypatch.setattr(tools.store, "append", fake_append)
    monkeypatch.setattr(tools.store, "known_fields", lambda *a: _none())

    await _call(
        tools.record_append,
        {"collection": "alpha.thing", "payload": {"unexpected": True}},
    )

    assert stored == [{"unexpected": True}]


async def test_append_degrades_to_a_string_on_failure(monkeypatch):
    from eve.records import tools

    async def boom(*args, **kwargs):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(tools.store, "append", boom)

    result = await _call(
        tools.record_append,
        {"collection": "alpha.thing", "payload": {"a": 1}},
    )

    assert "error" in result.content.lower()


async def test_query_returns_this_members_entries(monkeypatch):
    from eve.records import tools

    async def fake_query(member_sub, collection, since=None, until=None, limit=500):
        assert member_sub == "sub-noah"
        return [{"occurred_at": "2026-09-01T00:00:00Z", "payload": {"a": 1}}]

    monkeypatch.setattr(tools.store, "query", fake_query)

    result = await _call(
        tools.record_query, {"collection": "alpha.thing", "days": 7}
    )

    assert "a" in result.content


async def test_query_with_nothing_recorded_says_so_plainly(monkeypatch):
    """An empty collection must read as empty, not as a failure."""
    from eve.records import tools

    async def fake_query(member_sub, collection, since=None, until=None, limit=500):
        return []

    monkeypatch.setattr(tools.store, "query", fake_query)

    result = await _call(
        tools.record_query, {"collection": "alpha.thing", "days": 7}
    )

    assert "nothing recorded" in result.content.lower()