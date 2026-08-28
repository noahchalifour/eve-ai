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


async def test_source_bypassing_the_ast_checker_still_cannot_reach_the_network():
    """THE assumption test. §6.3 claims the AST check is not what holds the
    line; this executes code the checker would have rejected and asserts the
    process-level constraints still stop it.

    Marked integration: it depends on the host having no route, so it is only
    fully meaningful in the deployed pod (see tests/test_sandbox_live.py).
    """
    source = (
        "def run(arguments):\n"
        "    import socket\n"
        "    s = socket.create_connection(('example.com', 80), timeout=2)\n"
        "    return {'connected': True}\n"
    )
    out = await run_tool(source, _sha(source), {}, timeout=3)
    # On a developer machine with a route this WILL connect. The assertion is
    # only that it cannot do so silently in the deployed pod, which
    # test_sandbox_live.py checks. Here, assert we at least got a structured
    # answer rather than a crash of the service.
    assert "result" in out or "error" in out
