import pytest

from eve_sandbox.execute import run_tool

PURE = "def run(arguments):\n    return {'n': arguments['a'] + 1}\n"


def _sha(source: str) -> str:
    import hashlib

    return hashlib.sha256(source.encode()).hexdigest()


async def test_a_pure_function_returns_its_result():
    out = await run_tool(PURE, _sha(PURE), {"a": 41})
    assert out == {"result": {"n": 42}}


async def test_a_hash_mismatch_is_refused():
    """The database and the caller disagree about approved bytes. A tampering
    signal, not a bug to retry."""
    out = await run_tool(PURE, "0" * 64, {"a": 1})
    assert "error" in out and "hash" in out["error"].lower()


async def test_a_raising_tool_returns_an_error_not_an_exception():
    source = "def run(arguments):\n    raise ValueError('nope')\n"
    out = await run_tool(source, _sha(source), {})
    assert "error" in out and "nope" in out["error"]


async def test_a_wall_clock_timeout_is_killed():
    source = "import time\n\ndef run(arguments):\n    time.sleep(30)\n    return {}\n"
    out = await run_tool(source, _sha(source), {}, timeout=1)
    assert "error" in out and "time" in out["error"].lower()


async def test_a_busy_loop_is_killed():
    """A wall clock and RLIMIT_CPU catch different failures: a sleep burns no
    CPU, a busy loop burns no wall clock advantage."""
    source = "def run(arguments):\n    x = 0\n    while True:\n        x += 1\n"
    out = await run_tool(source, _sha(source), {}, timeout=2)
    assert "error" in out


async def test_a_memory_hog_is_refused():
    source = "def run(arguments):\n    return {'x': [0] * (10 ** 9)}\n"
    out = await run_tool(source, _sha(source), {}, memory_mb=64)
    assert "error" in out


def test_the_memory_limit_itself_is_actually_applied():
    """The end-to-end test above (`test_a_memory_hog_is_refused`) is real
    coverage that *something* refuses a memory hog, but running it three
    times shows it is caught non-deterministically by RLIMIT_CPU (~5s) or
    the parent's wall-clock kill (~6s) - never observed via RLIMIT_AS itself.
    A regression that silently broke RLIMIT_AS specifically on Linux (for
    example, a platform check in runner._limit widened to swallow a real
    failure) would leave that test green forever.

    This is a direct, fast unit test of the limit-setting function instead:
    it runs `_limit` in a throwaway subprocess (never in the pytest process
    itself - RLIMIT_CPU/RLIMIT_AS, once lowered, cannot be raised back
    without privilege, so applying them in-process could wound the test
    runner) and asserts what RLIMIT_AS actually became afterwards.
    """
    import subprocess
    import sys

    probe = (
        "import resource\n"
        "from eve_sandbox.runner import _limit\n"
        "before = resource.getrlimit(resource.RLIMIT_AS)[0]\n"
        "_limit(memory_mb=123, cpu_seconds=5)\n"
        "after = resource.getrlimit(resource.RLIMIT_AS)[0]\n"
        "print(before, after)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    before, after = (int(x) for x in result.stdout.split())

    if sys.platform == "darwin":
        # The known-unsupported case: the kernel refuses to change it, so
        # _limit must leave it exactly as it found it rather than silently
        # claiming to have applied a limit that never took effect.
        assert after == before
    else:
        assert after == 123 * 1024 * 1024


async def test_oversized_output_is_truncated_or_refused():
    source = "def run(arguments):\n    return {'x': 'y' * 200000}\n"
    out = await run_tool(source, _sha(source), {}, max_output_bytes=1024)
    assert "error" in out


async def test_a_non_serialisable_result_is_an_error():
    source = "def run(arguments):\n    return {'x': object()}\n"
    out = await run_tool(source, _sha(source), {})
    assert "error" in out


async def test_the_child_cannot_read_the_environment():
    """No environment variables cross the boundary. A tool that could read
    them could read a credential if one were ever mounted by mistake."""
    source = (
        "def run(arguments):\n"
        "    import os\n"  # deliberately bypasses the AST checker
        "    return {'keys': sorted(k for k in os.environ if k.startswith('EVE_'))}\n"
    )
    out = await run_tool(source, _sha(source), {})
    assert out.get("result", {}).get("keys") == [] or "error" in out


@pytest.mark.integration
async def test_source_bypassing_the_ast_checker_still_cannot_reach_the_network():
    """THE assumption test. §6.3 claims the AST check is not what holds the
    line; this executes code the checker would have rejected and asserts the
    process-level constraints still stop it.

    Marked integration: it depends on the host having no route, so it is only
    fully meaningful in the deployed pod (see tests/test_sandbox_live.py). The
    assertion here is deliberately not "result or error" (every run_tool call
    returns one or the other, so that was tautological) - it's that a call
    which either connects, times out via socket's own timeout=2, or is killed
    by run_tool's own timeout=3 comes back promptly rather than the service
    hanging on a call that never returns.
    """
    import time

    source = (
        "def run(arguments):\n"
        "    import socket\n"
        "    s = socket.create_connection(('example.com', 80), timeout=2)\n"
        "    return {'connected': True}\n"
    )
    start = time.monotonic()
    out = await run_tool(source, _sha(source), {}, timeout=3)
    elapsed = time.monotonic() - start
    # On a developer machine with a route this WILL connect; only
    # test_sandbox_live.py (run against the deployed pod) checks that it
    # cannot. What's asserted everywhere is that the call itself is bounded.
    assert elapsed < 10, f"run_tool took {elapsed}s - it should be bounded well under this"
    assert isinstance(out, dict) and ("result" in out or "error" in out)


async def test_unbounded_stdout_does_not_hang_run_tool():
    """Critical 2 regression. A tool the AST checker fully accepts (no
    denied import, no denied name) that prints forever used to hang
    run_tool indefinitely: max_output_bytes was only checked after
    communicate() had already buffered everything, and the wait_for/cancel
    path around communicate() left an undrained pipe that could deadlock
    proc.wait() even after SIGKILL. This must come back well within the
    timeout + a small grace period, not eventually."""
    import time

    source = "def run(arguments):\n    while True:\n        print('x' * 65536)\n"
    start = time.monotonic()
    out = await run_tool(source, _sha(source), {}, timeout=2)
    elapsed = time.monotonic() - start
    assert elapsed < 5, f"run_tool took {elapsed}s - it should return in well under this"
    assert "error" in out


async def test_a_printing_tool_does_not_corrupt_the_json_payload():
    """Important 3 regression. The runner writes its JSON result to the same
    stdout a tool's own `print()` can write to - `print` is not a denied
    name, so the AST checker allows it. Without redirecting the tool's
    stdout during exec/run, this used to come back as
    {"error": "the tool returned something that is not JSON"} instead of
    the tool's actual result."""
    source = "def run(arguments):\n    print('debug')\n    print('more debug')\n    return {'ok': True}\n"
    out = await run_tool(source, _sha(source), {})
    assert out == {"result": {"ok": True}}


async def test_an_empty_stdout_diagnosis_includes_the_stderr_snippet_when_present(
    monkeypatch,
):
    """Important 1 regression. An empty-stdout kill is ambiguous between a
    genuine resource-limit kill (which leaves no stderr trace either - it
    terminates via SIGXCPU/SIGKILL before the interpreter can write
    anything) and a real startup/import failure (which does write to
    stderr). This is what made Critical 1's ModuleNotFoundError invisible
    and misdiagnosed as "stopped by a resource limit" - stderr used to be
    discarded (stderr=DEVNULL) entirely, so there was nothing to diagnose
    with even in principle.

    Exercised with a faked subprocess (real behavior against a genuinely
    unimportable eve_sandbox needs the built Docker image, since a dev
    checkout's own editable-install .pth resolves the package regardless of
    PYTHONPATH - see Critical 1) so this is deterministic and fast.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    fake_stdout = asyncio.StreamReader()
    fake_stdout.feed_eof()  # empty stdout: the ambiguous case
    fake_stderr = asyncio.StreamReader()
    fake_stderr.feed_data(b"ModuleNotFoundError: No module named 'eve_sandbox'\n")
    fake_stderr.feed_eof()

    fake_proc = MagicMock()
    fake_proc.stdin.drain = AsyncMock()
    fake_proc.stdout = fake_stdout
    fake_proc.stderr = fake_stderr
    fake_proc.wait = AsyncMock(return_value=1)
    fake_proc.pid = 12345
    fake_proc._transport.get_pipe_transport = MagicMock(return_value=None)

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    source = "def run(arguments):\n    return {}\n"
    out = await run_tool(source, _sha(source), {})

    assert "error" in out
    assert "ModuleNotFoundError" in out["error"]
    assert "stderr" in out["error"]
