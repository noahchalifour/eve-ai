"""Shared test fixtures.

`get_settings`, `get_model`, `get_family`, `load_persona` and
`get_tools_settings` are all `lru_cache`d process-wide singletons, and all
five are settings-derived. Tests that mutate env vars to exercise
settings-dependent behavior (e.g. `test_model_is_pointed_at_litellm`) clear
caches before use but leave the mutated singleton cached afterward, which
would otherwise leak into every later test in the session. Clearing every
one of them around every test keeps them isolated regardless of run order -
most tests monkeypatch the importing module's reference instead, which works
but does not generalise.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time

import httpx
import pytest
import uvicorn
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from eve.context import load_persona
from eve.family import get_family
from eve.models import get_model
from eve.settings import get_settings
from eve.skills import mcp_registry
from eve_tools.settings import get_tools_settings

_CACHED = (get_settings, get_model, get_family, load_persona, get_tools_settings)


class FakeToolCallingModel(GenericFakeChatModel):
    """`GenericFakeChatModel` raises `NotImplementedError` from `bind_tools` -
    fine before Phase 3, when nothing in the graph called it. Every model in
    `eve`'s loop and every specialist's loop binds tools unconditionally now
    (Task 13), so every graph-level test needs a fake that tolerates it."""

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture(autouse=True)
def _clear_caches():
    for cached in _CACHED:
        cached.cache_clear()
    # `mcp_registry._REGISTERED` is process-lifetime mutable state, same
    # leak shape as the lru_caches above: a test that registers a spec
    # (test_skills_registry.py's round-trip test) would otherwise leave it
    # for every later test that calls the real `registered_mcp_tools()`.
    mcp_registry._REGISTERED.clear()
    yield
    for cached in _CACHED:
        cached.cache_clear()
    mcp_registry._REGISTERED.clear()


SERVER_URL = "http://127.0.0.1:2026"


@pytest.fixture(scope="session")
def aegra_server():
    """Start `aegra serve` against the docker-compose Postgres and Redis.

    AUTH_TYPE=custom is required: aegra_api's `get_auth_backend()` (installed
    package, `aegra_api/core/auth_middleware.py`) accepts only "noop" or
    "custom" and falls back to "noop" with a warning for anything else, but
    in this installed version (aegra-api 0.10.3) both branches construct the
    same `LangGraphAuthBackend`, which loads `aegra.json`'s `auth.path`
    regardless of AUTH_TYPE's value. "custom" is set anyway as the
    semantically correct declared value for a project with a custom auth
    handler, since a future aegra-api version may start gating on it.
    """
    env = {
        **os.environ,
        "EVE_ENV": "development",
        "EVE_AUTH_MODE": "dev",
        "EVE_DEV_TOKENS": '{"tok-noah": "sub-noah", "tok-kid": "sub-kid"}',
        "EVE_FAMILY_FILE": "tests/fixtures/family.yaml",
        "EVE_PROMPT_FILE": "prompts/eve.md",
        # Ports 15432/16379 rather than the defaults 5432/6379: see the
        # comment in docker-compose.test.yml. Only the host side changed.
        "DATABASE_URL": "postgresql://eve:eve@127.0.0.1:15432/eve",
        "REDIS_BROKER_ENABLED": "true",
        "REDIS_URL": "redis://127.0.0.1:16379/0",
        "AUTH_TYPE": "custom",
        # Phase 4: the impersonation credential the ambient integration tests
        # present. Length matters — Settings refuses anything under 32.
        "EVE_AMBIENT_TOKEN": "ambient-integration-token-0123456789abcdef",
    }
    # `start_new_session=True` puts `uv` and everything it execs/spawns (the
    # `aegra` CLI, and the uvicorn worker it in turn launches) into their own
    # process group. Empirically, `uv run aegra serve` spawns uvicorn as a
    # separate child rather than exec'ing into it, so a plain
    # `proc.terminate()` only kills the top-level `uv` process: uvicorn is
    # reparented to launchd (PID 1) and keeps running, still bound to port
    # 2026. Killing the whole group on teardown is what actually stops it.
    proc = subprocess.Popen(
        ["uv", "run", "aegra", "serve"], env=env, start_new_session=True
    )

    def _terminate() -> None:
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
            proc.wait(timeout=10)

    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            if httpx.get(f"{SERVER_URL}/ready", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        _terminate()
        raise RuntimeError("aegra did not become ready within 90s")
    yield SERVER_URL
    _terminate()


EVE_TOOLS_URL = "http://127.0.0.1:18090"


@pytest.fixture(scope="session")
def stub_home_assistant():
    """A stand-in for the real home lab's Home Assistant instance, run
    in-process on a background thread rather than as a subprocess: it has no
    dependencies of its own to isolate, and `eve_tools_server` below needs
    it started and torn down as an ordinary session fixture dependency."""
    from tests.fixtures.stub_home_assistant import app as stub_app

    config = uvicorn.Config(stub_app, host="127.0.0.1", port=18091, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if httpx.get("http://127.0.0.1:18091/api/states/x", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    else:
        raise RuntimeError("stub Home Assistant did not start")
    yield "http://127.0.0.1:18091"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def eve_tools_server(stub_home_assistant):
    """Start the real `eve-tools` service against the stub Home Assistant
    above. `start_new_session=True` and killing the whole process group on
    teardown mirrors `aegra_server`: `uv run uvicorn` spawns its worker as a
    separate child rather than exec'ing into it, so a plain terminate leaks
    it."""
    env = {
        **os.environ,
        "EVE_TOOLS_API_KEY": "test-key",
        "EVE_TOOLS_HOME_ASSISTANT_URL": stub_home_assistant,
        "EVE_TOOLS_HOME_ASSISTANT_TOKEN": "unused-in-stub",
    }
    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "eve_tools.app:app", "--host", "127.0.0.1", "--port", "18090"],
        env=env,
        start_new_session=True,
    )

    def _terminate():
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)

    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if httpx.get(f"{EVE_TOOLS_URL}/healthz", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    else:
        _terminate()
        raise RuntimeError("eve-tools did not become ready within 20s")
    yield EVE_TOOLS_URL
    _terminate()
