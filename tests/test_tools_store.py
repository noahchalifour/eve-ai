import pytest

pytestmark = pytest.mark.integration

SOURCE = "def run(arguments):\n    return {'ok': True}\n"


@pytest.fixture
async def pool(monkeypatch):
    monkeypatch.setenv(
        "EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve"
    )
    from eve.memory import db
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    p = await db.get_pool()
    async with p.connection() as conn:
        await conn.execute("TRUNCATE eve_tool")
    yield p
    await db.close_pool()


async def test_propose_stores_the_source_and_its_hash(pool):
    from eve.tools_authoring.store import by_id, propose

    tool_id = await propose(
        name="amortise", description="d", args_schema={"properties": {}},
        source=SOURCE, proposed_by="sub-noah", thread_id="t1", run_id="r1",
    )
    row = await by_id(tool_id)
    assert row["source"] == SOURCE
    assert len(row["source_sha256"]) == 64
    assert row["approved_at"] is None


async def test_approve_then_live(pool):
    from eve.tools_authoring.store import approve, live_tools, propose

    tool_id = await propose(
        name="amortise", description="d", args_schema={}, source=SOURCE,
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    assert await live_tools() == []
    assert await approve(tool_id, "sub-noah") is True
    assert [t["name"] for t in await live_tools()] == ["amortise"]


async def test_only_one_live_approved_version_per_name(pool):
    """The partial unique index IS the approval invariant in the schema."""
    import psycopg

    from eve.tools_authoring.store import approve, propose

    first = await propose(
        name="amortise", description="d", args_schema={}, source=SOURCE,
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    second = await propose(
        name="amortise", description="d2", args_schema={},
        source=SOURCE + "# v2\n", proposed_by="sub-noah",
        thread_id=None, run_id=None,
    )
    await approve(first, "sub-noah")
    with pytest.raises(psycopg.errors.UniqueViolation):
        await approve(second, "sub-noah")


async def test_the_old_version_keeps_serving_until_the_new_one_is_approved(pool):
    from eve.tools_authoring.store import approve, live_tools, propose

    first = await propose(
        name="amortise", description="v1", args_schema={}, source=SOURCE,
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    await approve(first, "sub-noah")
    await propose(
        name="amortise", description="v2", args_schema={},
        source=SOURCE + "# v2\n", proposed_by="sub-noah",
        thread_id=None, run_id=None,
    )
    live = await live_tools()
    assert len(live) == 1 and live[0]["description"] == "v1"


async def test_reject_records_why_and_never_approves(pool):
    from eve.tools_authoring.store import by_id, live_tools, propose, reject

    tool_id = await propose(
        name="amortise", description="d", args_schema={}, source=SOURCE,
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    await reject(tool_id, "reads a file")
    row = await by_id(tool_id)
    assert row["rejected_why"] == "reads a file"
    assert row["approved_at"] is None
    assert await live_tools() == []


async def test_revoke_frees_the_name_for_a_replacement(pool):
    from eve.tools_authoring.store import approve, live_tools, propose, revoke

    first = await propose(
        name="amortise", description="v1", args_schema={}, source=SOURCE,
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    await approve(first, "sub-noah")
    assert await revoke("amortise", "wrong") == 1
    assert await live_tools() == []

    second = await propose(
        name="amortise", description="v2", args_schema={},
        source=SOURCE + "# v2\n", proposed_by="sub-noah",
        thread_id=None, run_id=None,
    )
    assert await approve(second, "sub-noah") is True


async def test_revoke_all(pool):
    from eve.tools_authoring.store import approve, live_tools, propose, revoke_all

    for name in ("a", "b"):
        tool_id = await propose(
            name=name, description="d", args_schema={},
            source=SOURCE + f"# {name}\n", proposed_by="sub-noah",
            thread_id=None, run_id=None,
        )
        await approve(tool_id, "sub-noah")
    assert await revoke_all("incident") == 2
    assert await live_tools() == []
