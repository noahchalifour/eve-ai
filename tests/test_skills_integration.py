"""tests/test_skills_integration.py"""
import os

import pytest

from eve.skills.registry import load_skills
from eve.skills.search import rank_skills
from eve.settings import get_settings

pytestmark = pytest.mark.integration


def test_the_example_skill_loads_from_disk():
    get_settings.cache_clear()
    skills = load_skills()
    names = [s.name for s in skills]
    assert "greet-warmly" in names


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("EVE_LIVE_TESTS") != "1",
    reason="set EVE_LIVE_TESTS=1 to run against the real embedding model",
)
async def test_search_skills_finds_the_example_skill_for_a_relevant_query():
    get_settings.cache_clear()
    skills = load_skills()
    ranked = await rank_skills("how should I say hello", skills, top_k=1)
    assert ranked[0].name == "greet-warmly"


# eve/state.py: InjectedState validates the whole EveState/MemberContext
# shape strictly when a tool is invoked directly via `.ainvoke` (unlike a
# plain TypedDict at runtime) - the same full-state convention
# tests/test_specialists_base.py and tests/test_skills_search.py already
# follow.
_MEMBER = {
    "sub": "sub-noah",
    "name": "Noah",
    "role": "adult",
    "timezone": "America/Toronto",
    "permissions": [],
    "local_time": "2026-08-27 09:00 EDT",
}
_STATE = {
    "messages": [],
    "member": _MEMBER,
    "system_prompt": "",
    "memory": None,
    "dynamic_tools": [],
}


@pytest.fixture
async def clean_pool(monkeypatch):
    monkeypatch.setenv(
        "EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve"
    )
    monkeypatch.setenv("EVE_SELF_AUTHORING_ENABLED", "true")
    from eve.memory import db
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    pool = await db.get_pool()
    async with pool.connection() as conn:
        await conn.execute("TRUNCATE eve_memory")
    yield pool
    await db.close_pool()


async def test_a_rule_reaches_the_next_turns_prompt_then_is_revoked(clean_pool):
    """DoD 1, 4 and 8: authored rule -> next turn's prompt -> revoked -> gone,
    with the row surviving as the audit trail."""
    from eve.context import build_system_prompt
    from eve.memory.recall import recall
    from eve.memory.store import add
    from eve.skills.cli import authored, revoke

    rule_id = await add(
        layer="rule", scope_kind="member", scope_id="sub-noah",
        kind="preference", content="Lead with the number.",
        source_thread="t1",
    )

    state = {"member": {"sub": "sub-noah"}, "messages": []}
    out = await recall(state, {"configurable": {"thread_id": "t2"}})
    member = {
        "sub": "sub-noah", "name": "Noah", "role": "adult",
        "timezone": "America/Toronto", "permissions": [],
        "local_time": "2026-08-27 09:00 EDT",
    }
    assert "Lead with the number." in build_system_prompt("P", member, out["memory"])

    listed = await authored()
    assert rule_id in {str(r.id) for r in listed}

    await revoke(rule_id, "test")

    out = await recall(state, {"configurable": {"thread_id": "t2"}})
    assert out["memory"]["rules"] == []
    assert rule_id not in {str(r.id) for r in await authored()}

    # The row survives - Phase 5b reads this history.
    async with clean_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT superseded_why FROM eve_memory WHERE id = %s", (rule_id,)
        )
        assert (await cur.fetchone())[0].startswith("revoked by operator")


async def test_an_authored_procedure_is_retrievable_in_another_thread(clean_pool):
    """DoD 2: write_skill in one thread, search_skills finds it in another."""
    from eve.skills import search as search_mod
    from eve.skills.authoring import write_skill

    async def embed_query(text):
        return [1.0] + [0.0] * 1535

    search_mod.embed_query = embed_query

    await write_skill.ainvoke(
        {
            "name": "book-the-dog-sitter",
            "description": "How to book the dog sitter.",
            "content": "1. Text Sam.",
            "state": _STATE,
        },
        config={"configurable": {"thread_id": "t1", "run_id": "r1"}},
    )

    command = await search_mod.search_skills.ainvoke(
        {
            "name": "search_skills",
            "args": {"query": "dog sitter", "state": _STATE},
            "id": "c1",
            "type": "tool_call",
        }
    )
    assert "1. Text Sam." in command.update["messages"][0].content


async def test_write_skill_twice_supersedes_and_records_the_chain(clean_pool):
    """DoD 3: the superseded_by chain records the revision."""
    from eve.skills.authoring import write_skill

    args = {
        "name": "book-the-dog-sitter",
        "state": _STATE,
    }
    await write_skill.ainvoke(
        {**args, "description": "v1", "content": "1. Text Sam."},
        config={"configurable": {"thread_id": "t1"}},
    )
    await write_skill.ainvoke(
        {**args, "description": "v2", "content": "1. Call Sam."},
        config={"configurable": {"thread_id": "t2"}},
    )

    async with clean_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM eve_memory WHERE layer='procedure'"
            " AND superseded_by IS NOT NULL"
        )
        assert (await cur.fetchone())[0] == 1
        cur = await conn.execute(
            "SELECT count(*) FROM eve_memory WHERE layer='procedure'"
            " AND superseded_why IS NULL"
        )
        assert (await cur.fetchone())[0] == 1
