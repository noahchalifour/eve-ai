"""Shared test fixtures.

`get_settings`, `get_model`, `get_family` and `load_persona` are all
`lru_cache`d process-wide singletons, and all four are settings-derived.
Tests that mutate env vars to exercise settings-dependent behavior (e.g.
`test_model_is_pointed_at_litellm`) clear caches before use but leave the
mutated singleton cached afterward, which would otherwise leak into every
later test in the session. Clearing every one of them around every test keeps
them isolated regardless of run order - most tests monkeypatch the importing
module's reference instead, which works but does not generalise.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time

import httpx
import pytest

from eve.context import load_persona
from eve.family import get_family
from eve.models import get_model
from eve.settings import get_settings

_CACHED = (get_settings, get_model, get_family, load_persona)


@pytest.fixture(autouse=True)
def _clear_caches():
    for cached in _CACHED:
        cached.cache_clear()
    yield
    for cached in _CACHED:
        cached.cache_clear()


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
