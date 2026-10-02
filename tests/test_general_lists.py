"""Lists on the record store (ENG-372), with the store faked in memory."""
from __future__ import annotations

import pytest

from eve.general import lists

NOAH = {"sub": "sub-noah", "permissions": ["memory.write_shared"]}
KID = {"sub": "sub-kid", "permissions": []}


def _cfg(member):
    return {"configurable": {"member": member}}


@pytest.fixture
def db(monkeypatch):
    rows: dict[tuple[str, str], dict[str, dict]] = {}

    async def append(owner, collection, payload, occurred_at=None, key=None):
        bucket = rows.setdefault((owner, collection), {})
        if key in bucket:
            return {"deduped": True}
        bucket[key] = payload
        return {"deduped": False}

    async def query(owner, collection, since=None, until=None, limit=500):
        bucket = rows.get((owner, collection), {})
        # store.query is newest first
        return [{"key": k, "payload": v} for k, v in reversed(list(bucket.items()))]

    async def delete_keys(owner, collection, keys):
        bucket = rows.get((owner, collection), {})
        return [k for k in keys if bucket.pop(k, None) is not None]

    async def clear(owner, collection):
        return len(rows.pop((owner, collection), {}))

    async def collections_with_prefix(owner, prefix):
        return sorted(c for (o, c) in rows if o == owner and c.startswith(prefix))

    for name, fn in dict(append=append, query=query, delete_keys=delete_keys, clear=clear,
                         collections_with_prefix=collections_with_prefix).items():
        monkeypatch.setattr(lists.store, name, fn)
    return rows


async def test_add_is_idempotent_and_case_insensitive(db):
    out = await lists.list_add.ainvoke({"list_name": "Groceries", "items": ["Milk", "eggs", "milk"]}, _cfg(NOAH))
    assert out == "Added Milk, eggs to your groceries list."
    out = await lists.list_add.ainvoke({"list_name": "groceries list", "items": ["MILK"]}, _cfg(NOAH))
    assert out == "Already on it: MILK."
    assert set(db[("sub-noah", "list.groceries")]) == {"milk", "eggs"}


async def test_show_lists_in_the_order_added(db):
    await lists.list_add.ainvoke({"list_name": "todo", "items": ["one", "two", "three"]}, _cfg(NOAH))
    out = await lists.list_show.ainvoke({"list_name": "todo"}, _cfg(NOAH))
    assert out == "Your todo list:\n- one\n- two\n- three"
    assert await lists.list_show.ainvoke({"list_name": "*"}, _cfg(NOAH)) == "Lists: todo"


async def test_remove_reports_missing_items(db):
    await lists.list_add.ainvoke({"list_name": "todo", "items": ["one"]}, _cfg(NOAH))
    out = await lists.list_remove.ainvoke({"list_name": "todo", "items": ["ONE", "two"]}, _cfg(NOAH))
    assert out == "Removed ONE from your todo list. Not on it: two."


async def test_household_list_is_shared_and_write_needs_the_grant(db):
    await lists.list_add.ainvoke(
        {"list_name": "groceries", "items": ["milk"], "scope": "household"}, _cfg(NOAH))
    # Another member reads it.
    out = await lists.list_show.ainvoke({"list_name": "groceries", "scope": "household"}, _cfg(KID))
    assert out == "The household groceries list:\n- milk"
    # But cannot write without memory.write_shared.
    denied = await lists.list_add.ainvoke(
        {"list_name": "groceries", "items": ["candy"], "scope": "household"}, _cfg(KID))
    assert denied.startswith("Permission denied")
    assert set(db[(lists.HOUSEHOLD_OWNER, "list.groceries")]) == {"milk"}
    # Personal lists never see household items.
    assert "empty" in await lists.list_show.ainvoke({"list_name": "groceries"}, _cfg(NOAH))


async def test_clear_and_validation(db):
    await lists.list_add.ainvoke({"list_name": "todo", "items": ["a", "b"]}, _cfg(NOAH))
    assert await lists.list_clear.ainvoke({"list_name": "todo"}, _cfg(NOAH)) == "Cleared your todo list (2 items)."
    assert (await lists.list_add.ainvoke({"list_name": "", "items": ["a"]}, _cfg(NOAH))).startswith("error:")
    assert (await lists.list_add.ainvoke({"list_name": "x", "items": ["  "]}, _cfg(NOAH))).startswith("error:")
    assert (await lists.list_add.ainvoke({"list_name": "x", "items": ["a"], "scope": "everyone"}, _cfg(NOAH))).startswith("error:")
    assert (await lists.list_add.ainvoke({"list_name": "x", "items": ["z" * 300]}, _cfg(NOAH))).startswith("error:")
