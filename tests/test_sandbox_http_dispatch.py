"""The one test in this suite that hits the real, uvicorn-run eve-sandbox
service over HTTP - via the `eve_sandbox_server` fixture (tests/conftest.py,
Task 10), which every other test left unused until now.

Every other integration test that touches eve-sandbox (see
test_tools_integration.py) calls eve_sandbox.execute.run_tool in-process,
which never exercises the real subprocess boundary a deployed pod actually
serves through - `execute.py`'s asyncio.create_subprocess_exec call is
identical either way, but only a real HTTP round trip against a service that
was itself started as a subprocess (`uv run uvicorn ...`) puts that call
under the same process-tree shape production does. This test exercises
propose -> approve -> sandbox_specs -> a real /invoke POST, end to end, over
the process/HTTP boundary.

What this test does NOT verify, despite exercising a real subprocess and a
real HTTP call: that the *built Docker image* can import its own package.
`eve_sandbox_server` starts the service with `uv run uvicorn ...` from this
dev checkout, and the editable `uv sync` install drops a .pth file into
.venv/lib/python3.12/site-packages that puts src/ on sys.path independent of
PYTHONPATH or any interpreter isolation flag - so a bug shaped exactly like
Critical 1 from the whole-branch review (`python -I` silently discarding
PYTHONPATH, so a real deployed pod's `sys.executable -I -m
eve_sandbox.runner` subprocess call always failed with ModuleNotFoundError)
would NOT be caught here: the very .pth file the built image has no
equivalent of masks it in a dev checkout regardless of PYTHONPATH/isolation
flags. A previous re-reviewer proved this and flagged an earlier version of
this docstring, which claimed the opposite, as false. The regression test
that *does* cover the built image is tests/test_sandbox_docker_image.py,
marked `docker` (it builds the real image with `docker build` and runs a
container from it - too slow to want by default, so it needs `-m docker`
explicitly, mirroring how `-m integration` and `-m live` are opt-in here).
"""

import httpx
import pytest

pytestmark = pytest.mark.integration

PURE = "def run(arguments):\n    return {'n': arguments['a'] + 1}\n"

# Matches the EVE_SANDBOX_API_KEY the eve_sandbox_server fixture sets for its
# subprocess's environment (tests/conftest.py).
_SANDBOX_API_KEY = "test-key-0123456789abcdef0123456789ab"


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


async def test_propose_approve_and_invoke_against_the_real_running_service(
    pool, eve_sandbox_server
):
    """DoD 1, 3, 8: propose -> approve -> sandbox_specs -> a real /invoke
    call over HTTP against the actual uvicorn-served eve_sandbox.app,
    asserting a correct result rather than an error."""
    from eve.tools_authoring.cli import approve_one
    from eve.tools_authoring.registry import sandbox_specs
    from eve.tools_authoring.store import propose

    tool_id = await propose(
        name="amortise", description="Amortise a loan.",
        args_schema={"properties": {"a": {"type": "integer"}}},
        source=PURE, proposed_by="sub-noah", thread_id="live-image-1", run_id="r1",
    )
    assert await approve_one(tool_id, "sub-noah") is True

    specs = await sandbox_specs()
    assert len(specs) == 1
    spec = specs[0]

    response = httpx.post(
        f"{eve_sandbox_server}/invoke",
        headers={"Authorization": f"Bearer {_SANDBOX_API_KEY}"},
        json={
            "tool": spec["tool_name"],
            "arguments": {"a": 41},
            "source": spec["source"],
            "source_sha256": spec["source_sha256"],
        },
        timeout=10,
    )
    assert response.status_code == 200
    assert response.json() == {"result": {"n": 42}}
