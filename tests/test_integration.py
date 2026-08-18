"""End-to-end integration tests against a live `aegra serve` instance.

Requires `docker compose -f docker-compose.test.yml up -d` (Postgres + Redis)
to be running. Marked `integration`, and `pyproject.toml`'s pytest `addopts`
deselects that marker, so a bare `pytest` skips these; select them explicitly
with `-m integration`, which overrides `addopts`.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from langgraph_sdk import get_client
from langgraph_sdk.errors import APIStatusError

pytestmark = pytest.mark.integration


def _client(url, token):
    return get_client(url=url, headers={"Authorization": f"Bearer {token}"})


async def test_health_endpoints_are_up(aegra_server):
    assert httpx.get(f"{aegra_server}/health", timeout=5).status_code == 200
    assert httpx.get(f"{aegra_server}/live", timeout=5).status_code == 200


async def test_unauthenticated_request_is_rejected(aegra_server):
    response = httpx.post(f"{aegra_server}/threads", json={}, timeout=10)
    assert response.status_code == 401


async def test_forged_token_is_rejected(aegra_server):
    response = httpx.post(
        f"{aegra_server}/threads",
        json={},
        headers={"Authorization": "Bearer tok-forged"},
        timeout=10,
    )
    assert response.status_code == 401


async def test_thread_is_created_and_stamped_with_its_owner(aegra_server):
    thread = await _client(aegra_server, "tok-noah").threads.create()
    assert thread["metadata"]["owner"] == "sub-noah"


async def test_a_member_cannot_read_another_members_thread(aegra_server):
    """A bare `pytest.raises(Exception)` would also pass on a network hiccup
    or a client-side validation error without ever exercising authorization.
    Assert the specific status observed, matching the 404-is-a-pass
    convention established by the cross-member resume/delete tests below."""
    thread = await _client(aegra_server, "tok-noah").threads.create()
    with pytest.raises(APIStatusError) as exc_info:
        await _client(aegra_server, "tok-kid").threads.get(thread["thread_id"])
    assert exc_info.value.status_code == 404


async def test_search_returns_only_the_callers_own_threads(aegra_server):
    """Must create a thread for BOTH members. With only `tok-kid` ever
    creating threads, `all(...)` over kid's results is vacuously true on a
    clean database regardless of whether the search filter does anything -
    it only had teeth here because of leftover rows from earlier runs. The
    non-empty check and the explicit "noah's thread_id is absent" assertion
    are what actually catch a broken filter."""
    noah_thread = await _client(aegra_server, "tok-noah").threads.create()
    kid_thread = await _client(aegra_server, "tok-kid").threads.create()
    kid_threads = await _client(aegra_server, "tok-kid").threads.search()
    assert len(kid_threads) > 0
    assert all(t["metadata"]["owner"] == "sub-kid" for t in kid_threads)
    kid_thread_ids = {t["thread_id"] for t in kid_threads}
    assert kid_thread["thread_id"] in kid_thread_ids
    assert noah_thread["thread_id"] not in kid_thread_ids


# --- Additional tests: cross-member bypass and the runs-resource assumption ---


async def test_cross_member_resume_is_rejected(aegra_server):
    """A family member must not be able to start a run on another member's
    thread (spec section 8.3's exact bypass). `only_own_threads` in
    src/eve/auth.py is documented to cover `create_run`, but empirically the
    refusal happens a layer earlier: aegra-api's `create_run` endpoint
    (aegra_api/api/runs.py) looks the thread up filtered by
    `ThreadORM.user_id == user.identity` before our custom auth handlers ever
    run, and returns 404 ("Thread not found") rather than reaching our
    handler at all. That is an acceptable, arguably better, refusal than
    401/403 - it declines to confirm the thread exists to a non-owner. The
    property under test is that the request is refused; 404 is asserted
    specifically (not "any non-2xx") so a future regression to 200 fails
    loudly."""
    thread = await _client(aegra_server, "tok-noah").threads.create()
    with pytest.raises(APIStatusError) as exc_info:
        await _client(aegra_server, "tok-kid").runs.create(
            thread["thread_id"],
            "eve",
            input={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert exc_info.value.status_code == 404


async def test_cross_member_delete_is_rejected(aegra_server):
    """A family member must not be able to delete another member's thread.

    Same finding as the resume test above: aegra-api's `delete_thread`
    queries `ThreadORM.user_id == user.identity` (ANDed with our custom
    handler's metadata filter) and returns 404 when the row doesn't match,
    before/alongside our handler's own filter."""
    thread = await _client(aegra_server, "tok-noah").threads.create()
    with pytest.raises(APIStatusError) as exc_info:
        await _client(aegra_server, "tok-kid").threads.delete(thread["thread_id"])
    assert exc_info.value.status_code == 404


async def test_run_is_not_blocked_by_authorization(aegra_server):
    """Starting a run on the owner's own thread must not be denied by the
    authorization layer. A 403 here means `deny_by_default` in src/eve/auth.py
    is failing closed on a resource/action Eve's core conversation path needs,
    and that path is broken.

    This asserts only the HTTP status. What the run then *does* is
    `test_a_turn_executes_our_graph_under_aegra` below - the two are separate
    because they fail for different reasons and a reader debugging one should
    not have to disentangle the other.
    """
    client = _client(aegra_server, "tok-noah")
    thread = await client.threads.create()
    try:
        await client.runs.create(
            thread["thread_id"],
            "eve",
            input={"messages": [{"role": "user", "content": "hi"}]},
        )
    except APIStatusError as exc:
        assert exc.status_code != 403


async def _run_to_completion(client, thread_id: str) -> dict:
    """Dispatch one turn and wait for the run to reach a terminal state.

    `runs.join` blocks until the worker finishes but returns `{}` on failure
    rather than raising, so the run is re-read afterwards for its real status.
    """
    run = await client.runs.create(
        thread_id, "eve", input={"messages": [{"role": "user", "content": "hi"}]}
    )
    await client.runs.join(thread_id, run["run_id"])
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        run = await client.runs.get(thread_id, run["run_id"])
        if run["status"] not in ("pending", "running"):
            return run
        await asyncio.sleep(0.5)
    raise AssertionError(f"run did not finish within 60s: status={run['status']}")


async def test_a_turn_executes_our_graph_under_aegra(aegra_server):
    """A dispatched turn must actually execute `load_context` under Aegra and
    reach the model call.

    This is the test that would have caught the `langgraph_auth_user` seam:
    Aegra injects a pydantic `aegra_api.models.auth.User` into
    `config["configurable"]["langgraph_auth_user"]`, not the plain dict the
    unit tests hand-build, so subscripting it raised
    `TypeError: 'User' object is not subscriptable` on every real run while
    every test on the branch still passed.

    There is no working LiteLLM key in this environment, so the turn cannot
    produce an assistant message - the `eve` node fails with a 401 from the
    proxy. That is a fact about the environment, not about our graph, so what
    is asserted here is everything up to and including that boundary:

    - `load_context` completed and its output was checkpointed, i.e. the
      member was resolved off the real Aegra principal and the persona was
      assembled (`values["member"]`, `values["system_prompt"]`);
    - the graph advanced to the `eve` node (`next == ["eve"]`), i.e. the turn
      reached the model call;
    - nothing in *our* code raised: no task failed inside `load_context`.

    Any failure in `load_context` - a `TypeError` from the principal shape, an
    unknown member, a missing persona file - fails all three.
    """
    client = _client(aegra_server, "tok-noah")
    thread = await client.threads.create()
    run = await _run_to_completion(client, thread["thread_id"])

    state = await client.threads.get_state(thread["thread_id"])
    values = state["values"]

    failed_in_our_graph = [
        task for task in state["tasks"] if task["name"] == "load_context"
    ]
    assert not failed_in_our_graph, (
        f"load_context did not complete: {failed_in_our_graph} "
        f"(run error: {run.get('error_message')})"
    )

    assert values["member"]["sub"] == "sub-noah"
    assert values["member"]["name"] == "Noah"
    assert values["member"]["permissions"] == ["home.control", "spend"]
    assert values["member"]["local_time"]
    assert "You are Eve" in values["system_prompt"]
    assert "Noah" in values["system_prompt"]

    if run["status"] == "success":
        # A future environment with a working key gets the whole turn.
        assert values["messages"][-1]["type"] == "ai"
    else:
        assert state["next"] == ["eve"], (
            "the turn did not reach the model call; "
            f"run status={run['status']} error={run.get('error_message')}"
        )
