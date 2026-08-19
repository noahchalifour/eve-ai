"""End-to-end memory through a live `aegra serve`.

The unit tests prove each part works against a fake. This proves the parts
are connected - which is the failure Phase 1 would have shipped if the live
tier had not existed.
"""

from __future__ import annotations

import os

import pytest
from langgraph_sdk import get_client

from eve.memory import db, store

pytestmark = pytest.mark.integration

requires_litellm_key = pytest.mark.skipif(
    not os.environ.get("EVE_LITELLM_API_KEY"),
    reason="requires a working EVE_LITELLM_API_KEY for the live Aegra turn",
)


@pytest.fixture
async def clean_memory(monkeypatch):
    monkeypatch.setenv(
        "EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve"
    )
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    pool = await db.get_pool()
    async with pool.connection() as conn:
        await conn.execute("TRUNCATE eve_memory")
    yield
    await db.close_pool()


async def _run_to_success(client, thread_id: str, content: str) -> None:
    run = await client.runs.create(
        thread_id,
        "eve",
        input={"messages": [{"role": "human", "content": content}]},
    )
    await client.runs.join(thread_id, run["run_id"])
    finished = await client.runs.get(thread_id, run["run_id"])
    assert finished["status"] == "success", finished.get("error_message")


@requires_litellm_key
async def test_a_fact_written_in_one_thread_is_recalled_in_another(
    aegra_server, clean_memory
):
    """DoD item 1. The single behaviour this whole phase exists to produce."""
    await store.add(
        layer="profile",
        scope_kind="member",
        scope_id="sub-noah",
        kind="fact",
        content="Noah is vegetarian",
        subject="noah",
        source_thread="source-thread",
    )
    client = get_client(
        url=aegra_server, headers={"Authorization": "Bearer tok-noah"}
    )
    thread = await client.threads.create()
    await _run_to_success(client, thread["thread_id"], "what should we eat?")

    # Assert on the graph state produced by recall. Re-reading the row directly
    # would pass even if Aegra never executed Eve's recall node at all.
    state = await client.threads.get_state(thread["thread_id"])
    profile = state["values"]["memory"]["profile"]
    assert [memory["content"] for memory in profile] == ["Noah is vegetarian"]


async def test_household_is_shared_but_another_members_profile_is_private(
    clean_memory,
):
    """DoD item 6: standing household facts are shared; profiles are not."""
    await store.add(
        layer="profile",
        scope_kind="member",
        scope_id="sub-noah",
        kind="fact",
        content="Noah is vegetarian",
    )
    await store.add(
        layer="household",
        scope_kind="household",
        scope_id="family",
        kind="fact",
        content="Trash goes out Sunday",
    )

    profile, household, _ = await store.load_always_on("sub-kid", "t1")
    assert profile == []
    assert [memory.content for memory in household] == ["Trash goes out Sunday"]


@requires_litellm_key
async def test_recall_survives_the_database_having_no_memories(
    aegra_server, clean_memory
):
    """An empty day-one store must produce a complete turn, not an exception."""
    client = get_client(
        url=aegra_server, headers={"Authorization": "Bearer tok-noah"}
    )
    thread = await client.threads.create()
    await _run_to_success(client, thread["thread_id"], "hello")

    state = await client.threads.get_state(thread["thread_id"])
    memory = state["values"]["memory"]
    assert memory["profile"] == []
    assert memory["household"] == []
    assert memory["episodic"] == []
    assert memory["digest"] is None
