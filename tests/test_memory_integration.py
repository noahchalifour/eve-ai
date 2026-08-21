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


@requires_litellm_key
async def test_a_superseded_fact_is_not_recalled_but_the_row_survives(
    aegra_server, clean_memory
):
    """DoD item 2. `extract` decides *when* to supersede via the REFLEX model
    (unprovisioned - see ADR 0004/Prerequisite P1), but the mechanism it calls
    is `store.supersede`, exercised directly here exactly as extract.py calls
    it. This proves recall honours `superseded_why IS NULL` end-to-end through
    a real Aegra turn, and that supersession retires the row instead of
    deleting it (ADR 0005) - both real requirements independent of REFLEX.
    """
    old_id = await store.add(
        layer="profile",
        scope_kind="member",
        scope_id="sub-noah",
        kind="fact",
        content="Noah is vegetarian",
        subject="noah",
    )
    new_id = await store.add(
        layer="profile",
        scope_kind="member",
        scope_id="sub-noah",
        kind="fact",
        content="Noah eats meat now",
        subject="noah",
    )
    await store.supersede(old_id, new_id, "contradicted")

    client = get_client(
        url=aegra_server, headers={"Authorization": "Bearer tok-noah"}
    )
    thread = await client.threads.create()
    await _run_to_success(client, thread["thread_id"], "what should we eat?")

    state = await client.threads.get_state(thread["thread_id"])
    profile = state["values"]["memory"]["profile"]
    assert [memory["content"] for memory in profile] == ["Noah eats meat now"]

    pool = await db.get_pool()
    async with pool.connection() as conn:
        content, superseded_by, superseded_why = await (
            await conn.execute(
                "SELECT content, superseded_by, superseded_why "
                "FROM eve_memory WHERE id = %s",
                (old_id,),
            )
        ).fetchone()
    # psycopg returns the uuid column as `uuid.UUID`, not the `str` `store.add`
    # returns - stringify rather than compare types that were never meant to
    # be the same.
    assert (content, str(superseded_by), superseded_why) == (
        "Noah is vegetarian",
        new_id,
        "contradicted",
    )


@requires_litellm_key
async def test_forgetting_a_fact_hard_deletes_it(aegra_server, clean_memory):
    """DoD item 3. As with supersession, the natural-language "forget that"
    trigger lives behind the unprovisioned REFLEX model; `store.forget` is the
    mechanism it calls, exercised directly. A tombstone that still holds the
    text is not forgetting (ADR 0005), so this asserts the row is gone, not
    merely retired.
    """
    forgotten_id = await store.add(
        layer="profile",
        scope_kind="member",
        scope_id="sub-noah",
        kind="fact",
        content="Noah's social security number is 000-00-0000",
        subject="noah",
    )
    await store.forget(forgotten_id)

    client = get_client(
        url=aegra_server, headers={"Authorization": "Bearer tok-noah"}
    )
    thread = await client.threads.create()
    await _run_to_success(client, thread["thread_id"], "what do you know about me?")

    state = await client.threads.get_state(thread["thread_id"])
    assert state["values"]["memory"]["profile"] == []

    pool = await db.get_pool()
    async with pool.connection() as conn:
        count = await (
            await conn.execute(
                "SELECT count(*) FROM eve_memory WHERE id = %s", (forgotten_id,)
            )
        ).fetchone()
    assert count == (0,)


@requires_litellm_key
async def test_recall_degrades_cleanly_and_reports_its_own_latency(
    aegra_server, clean_memory
):
    """DoD items 4 and 5, against the real deployed LiteLLM proxy rather than
    a fake. Gemini is not yet registered there (Prerequisite P1 is still
    open), so the vector arm genuinely fails on every live turn today - this
    is item 5's "forced embedding failure" happening for real, not simulated.
    `latency_ms` is recall's own contribution to TTFT, the same number
    `eve.recall.latency_ms` reports to Langfuse in production (ADR 0002).
    """
    client = get_client(
        url=aegra_server, headers={"Authorization": "Bearer tok-noah"}
    )
    thread = await client.threads.create()
    await _run_to_success(client, thread["thread_id"], "hello")

    memory = (await client.threads.get_state(thread["thread_id"]))["values"]["memory"]
    assert memory["vector_used"] is False
    # Generous ceiling: a real network round trip to a homelab proxy, not the
    # 150ms p50 operational target Langfuse tracks over a day. This guards
    # against a gross regression (e.g. the budget timeout stops firing),
    # not the SLA itself.
    assert memory["latency_ms"] < 1000
