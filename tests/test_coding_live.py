"""The only tier entitled to claim an agent x model pair works.

ADR 0004's original fallback plan died on an untested assumption about
exactly this kind of wire translation, and the fix was a live probe, not a
table. That is why there is no compatibility matrix in this repository -
there is this file.

Requires EVE_LIVE_TESTS=1, the real LiteLLM proxy, and a scratch repo named
by EVE_CODING_LIVE_REPO that Eve's GitHub account can push to.

This tier spends real subscription rate limits - the exact resource ADR
0004 protects - so the parametrisation stays small on purpose.
"""

import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("EVE_LIVE_TESTS") != "1", reason="live tier"),
]

REPO = os.environ.get("EVE_CODING_LIVE_REPO", "")
AUTH = {"Authorization": f"Bearer {os.environ.get('EVE_COMPUTER_API_KEY', '')}"}


@pytest.fixture
def box():
    from eve_computer import app as app_mod
    from eve_computer.acp import session

    session._SESSIONS.clear()
    session._semaphore = None
    with TestClient(app_mod.app) as client:
        yield client
    session._SESSIONS.clear()


@pytest.mark.skipif(not REPO, reason="set EVE_CODING_LIVE_REPO")
@pytest.mark.parametrize(
    ("agent", "model"),
    [
        ("codex", "chatgpt/gpt-5.6-sol"),
        ("codex", "chatgpt/gpt-5.6-luna"),
        ("claude", "anthropic/claude-sonnet-5"),
        ("opencode", "chatgpt/gpt-5.6-sol"),
    ],
)
def test_the_agent_reaches_litellm_and_completes_a_turn(box, agent, model):
    """A `wire_api` or provider-block mistake surfaces here and nowhere
    else. The goal is deliberately trivial: this asserts the pair TALKS,
    not that it codes well."""
    session_id = str(uuid.uuid4())
    created = box.post(
        "/sessions",
        json={
            "id": session_id, "agent": agent, "model": model, "repos": [REPO],
            "prompt": "Reply with the single word READY. Do not edit any files.",
        },
        headers=AUTH,
    )
    assert created.status_code == 202

    deadline = time.monotonic() + 300
    body: dict = {}
    while time.monotonic() < deadline:
        body = box.get(f"/sessions/{session_id}", headers=AUTH).json()
        if body["status"] in ("idle", "failed"):
            break
        time.sleep(2)

    box.delete(f"/sessions/{session_id}", headers=AUTH)

    assert body.get("status") == "idle", (
        f"{agent} on {model} did not complete a turn: {body.get('error')}"
    )
    assert any(t["role"] == "agent" and t["text"].strip() for t in body["turns"]), (
        f"{agent} on {model} produced no text - the classic signature of a "
        "proxy that accepted the request and stripped what mattered"
    )


async def test_the_real_catalogue_contains_no_ocp_models():
    """The deny-list, against the real proxy. ocp/* fails silently at
    runtime - the proxy strips tool definitions - so it must be refused
    before dispatch, not discovered afterwards."""
    from eve.coding import catalogue

    catalogue._reset_cache()
    models = await catalogue.available_models()

    assert models, "the real proxy returned no models"
    assert not [m for m in models if m.startswith("ocp/")]


async def test_every_parametrised_model_is_still_served():
    """Catches a model retirement (ADR 0004: `gpt-5.4` retired 2026-08-31)
    before it shows up as an unexplained session failure."""
    from eve.coding import catalogue

    catalogue._reset_cache()
    models = await catalogue.available_models()

    for model in ("chatgpt/gpt-5.6-sol", "chatgpt/gpt-5.6-luna", "anthropic/claude-sonnet-5"):
        assert model in models, f"{model} is no longer served by the proxy"
