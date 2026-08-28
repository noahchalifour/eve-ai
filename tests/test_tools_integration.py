import pytest

pytestmark = pytest.mark.integration

PURE = "def run(arguments):\n    return {'n': arguments['a'] + 1}\n"


@pytest.fixture
async def pool(monkeypatch):
    monkeypatch.setenv(
        "EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve"
    )
    monkeypatch.setenv("EVE_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("EVE_SANDBOX_API_KEY", "k" * 32)
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


async def test_eve_sandbox_imports_nothing_from_eve():
    """DoD 12. The sandbox is the one package that must be unable to reach
    anything: every import is a line that could be tricked into reading."""
    import pathlib

    offenders = []
    for path in pathlib.Path("src/eve_sandbox").rglob("*.py"):
        text = path.read_text()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import eve", "from eve")) and not stripped.startswith(
                ("import eve_sandbox", "from eve_sandbox")
            ):
                offenders.append(f"{path}: {stripped}")
    assert offenders == [], offenders


async def test_propose_approve_discover_invoke_end_to_end(pool, monkeypatch):
    """DoD 1, 3: the whole path, with the interrupt resolved by the CLI."""
    from eve.skills.materialize import materialize
    from eve.tools_authoring.cli import approve_one
    from eve.tools_authoring.registry import sandbox_specs
    from eve.tools_authoring.store import propose

    tool_id = await propose(
        name="amortise", description="Amortise a loan.",
        args_schema={"properties": {"a": {"type": "integer"}}},
        source=PURE, proposed_by="sub-noah", thread_id="t1", run_id="r1",
    )
    assert await approve_one(tool_id, "sub-noah") is True

    specs = await sandbox_specs()
    assert len(specs) == 1

    # Dispatch straight through the real executor rather than over HTTP: the
    # HTTP hop is covered by tests/test_sandbox_app.py.
    from eve_sandbox.execute import run_tool

    out = await run_tool(specs[0]["source"], specs[0]["source_sha256"], {"a": 41})
    assert out == {"result": {"n": 42}}

    built = materialize(specs[0])
    assert built.name == "sandbox_amortise"


async def test_a_changed_source_needs_a_fresh_approval(pool):
    """DoD 4: the old version keeps serving until the new one is approved."""
    from eve.tools_authoring.cli import approve_one
    from eve.tools_authoring.registry import sandbox_specs
    from eve.tools_authoring.store import propose

    first = await propose(
        name="amortise", description="v1", args_schema={}, source=PURE,
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    await approve_one(first, "sub-noah")
    await propose(
        name="amortise", description="v2", args_schema={},
        source=PURE + "# v2\n", proposed_by="sub-noah",
        thread_id=None, run_id=None,
    )
    specs = await sandbox_specs()
    assert len(specs) == 1 and specs[0]["description"] == "v1"


async def test_approve_refuses_source_that_fails_its_checks(pool):
    """DoD 7's first half: the checker runs again at approval time, so a row
    edited between propose and approve cannot slip through."""
    from eve.tools_authoring.cli import approve_one
    from eve.tools_authoring.store import propose

    tool_id = await propose(
        name="reader", description="d", args_schema={},
        source="import os\n\ndef run(arguments):\n    return {'x': os.getcwd()}\n",
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    with pytest.raises(SystemExit):
        await approve_one(tool_id, "sub-noah")


async def test_impure_source_fails_on_process_constraints_not_the_checker(pool):
    """DoD 7's second half, and the claim §6.3 rests on: with the AST checker
    bypassed entirely, the process-level constraints still hold.

    The environment is empty, so a tool reading EVE_* finds nothing even
    though `import os` succeeded.
    """
    import hashlib

    from eve_sandbox.execute import run_tool

    source = (
        "def run(arguments):\n"
        "    import os\n"
        "    return {'eve_vars': sorted(k for k in os.environ if k.startswith('EVE_'))}\n"
    )
    out = await run_tool(source, hashlib.sha256(source.encode()).hexdigest(), {})
    assert out["result"]["eve_vars"] == []


async def test_revoke_takes_effect_with_no_restart(pool):
    """DoD 9: load_skills is rebuilt per call, so a revoke lands immediately."""
    from eve.tools_authoring.cli import approve_one, revoke_one
    from eve.tools_authoring.registry import sandbox_specs
    from eve.tools_authoring.store import propose

    tool_id = await propose(
        name="amortise", description="d", args_schema={}, source=PURE,
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    await approve_one(tool_id, "sub-noah")
    assert len(await sandbox_specs()) == 1

    await revoke_one("amortise", "not needed")
    assert await sandbox_specs() == []
