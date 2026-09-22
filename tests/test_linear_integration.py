"""End to end with eve-computer and Linear both faked.

Asserts the seams the unit tests cannot: that the ordering contract holds
through the real handler, that a Linear retry dispatches exactly once, and
that an elicitation answered in Linear resumes the same session.
"""

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from eve_ambient import app as app_module

pytestmark = pytest.mark.integration

SECRET = "L" * 32


def _post(client, payload):
    body = json.dumps(payload).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/signals/linear",
        content=body,
        headers={"linear-signature": signature, "content-type": "application/json"},
    )


def _created(session_id="lin_sess_1"):
    return {
        "action": "created",
        "webhookTimestamp": int(time.time() * 1000),
        "agentSession": {
            "id": session_id,
            "issue": {"id": "lin_issue_1", "team": {"id": "lin_team_1"}},
            "creator": {"id": "lin_noah"},
            "guidance": "Work in owner/repo.",
            "promptContext": "Fix the login bug.",
        },
    }


@pytest.fixture
async def world(tmp_path, monkeypatch):
    """Fakes everything outside this repository: Linear's API, eve-computer,
    Aegra, and memory recall. The database is real."""
    roster = tmp_path / "family.yaml"
    roster.write_text(
        "members:\n"
        "  - sub: 'noah-sub'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    linear_id: 'lin_noah'\n"
        "    permissions: ['code.delegate']\n"
    )
    monkeypatch.setenv("EVE_FAMILY_FILE", str(roster))
    monkeypatch.setenv("EVE_LINEAR_ENABLED", "true")
    monkeypatch.setenv("EVE_LINEAR_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("EVE_LINEAR_REPO_ALLOWLIST", '["owner/repo"]')

    from eve.family import get_family
    from eve.settings import get_settings
    from eve_linear import activities, handler

    get_settings.cache_clear()
    get_family.cache_clear()

    # Ruling: the brief's own `world` fixture claims the database is real
    # but never wires one up. Following the pattern in
    # tests/test_ambient_integration.py and tests/test_coding_store.py: the
    # same scratch Postgres at 127.0.0.1:25432, migrated and truncated
    # before each test.
    monkeypatch.setenv(
        "EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:25432/eve"
    )
    from eve.memory import db as memory_db

    get_settings.cache_clear()
    get_family.cache_clear()
    await memory_db.close_pool()
    await memory_db.migrate()
    pool = await memory_db.get_pool()
    async with pool.connection() as conn:
        await conn.execute("TRUNCATE eve_coding_session")

    state = {"activities": [], "dispatches": [], "order": [], "killed": []}

    async def _fake_invoke(tool, arguments, **kwargs):
        if tool == "linear.create_activity":
            state["activities"].append(arguments["content"])
            state["order"].append("emit")
            return '{"success": true}'
        return '{"success": true}'

    async def _fake_create(session_id, agent, model, repos, goal):
        state["dispatches"].append(session_id)
        return "ok"

    async def _fake_recall(goal, member_sub):
        state["order"].append("recall")
        return ""

    async def _fake_thread():
        return "thread-1"

    async def _fake_validate(model, agent):
        return "claude-sonnet-5"

    async def _fake_kill(session_id):
        state["killed"].append(session_id)
        return "ok"

    monkeypatch.setattr(activities, "invoke", _fake_invoke)
    monkeypatch.setattr(handler, "invoke", _fake_invoke)
    monkeypatch.setattr(handler, "create_coding_session", _fake_create)
    monkeypatch.setattr(handler, "_recall_context", _fake_recall)
    monkeypatch.setattr(handler, "_create_thread", _fake_thread)
    monkeypatch.setattr(handler.catalogue, "validate", _fake_validate)
    monkeypatch.setattr(handler, "kill_coding_session", _fake_kill)

    yield state

    get_settings.cache_clear()
    get_family.cache_clear()
    await memory_db.close_pool()


@pytest.fixture
def client():
    with TestClient(app_module.app) as test_client:
        yield test_client


async def test_a_delegation_acknowledges_then_dispatches(client, world):
    response = _post(client, _created())
    assert response.status_code == 202

    # Give the background task a moment to run.
    time.sleep(0.5)

    assert world["activities"][0]["type"] == "thought"
    # The ordering contract, through the real handler this time.
    assert world["order"].index("emit") < world["order"].index("recall")
    assert len(world["dispatches"]) == 1


async def test_a_retried_created_dispatches_exactly_once(client, world):
    """Deviation from the brief's literal assertion, recorded here because
    it is load-bearing.

    The brief's Step 1 text asserts `len(world["dispatches"]) == 1`, but
    `handle_created` calls `create_coding_session` (eve-computer, which is
    what `world["dispatches"]` records) before `store.create_session`
    (Task 7's docstring: "WHY THE ROW IS WRITTEN AFTER THE BOX ACCEPTS").
    A retried webhook therefore reaches eve-computer twice; the unique
    constraint only fires on the second, later, database write, by which
    point the second box session already exists and `world["dispatches"]`
    already has two entries. The brief's own Step 3 handler code and its
    self-review acknowledge this ordering directly ("the box is now running
    a session with no row ... stop it rather than leaving an orphan burning
    tokens"), so a second eve-computer dispatch on retry is the documented,
    expected behaviour, not a bug this task fixes. What Step 3 actually
    guarantees, and what this test asserts instead, is the invariant the
    brief's own inline comment states: the unique index means the retry
    never produces a second *supervised* session, i.e. a second row in
    `eve_coding_session`.

    The row-count assertion alone is NOT load-bearing for Task 10's actual
    fix: the pre-existing unique index (Task 4, covered by
    `tests/test_coding_store.py::test_two_sessions_cannot_share_one_linear_session_id`)
    guarantees it on its own, even with Task 10's try/except and
    `kill_coding_session` call entirely reverted (the raw `UniqueViolation`
    would just propagate to `_handle_linear_in_background`'s outer
    `except Exception` in `eve_ambient/app.py` and get swallowed there,
    leaving the row count at 1 regardless). What this test additionally
    asserts now, the `kill_coding_session` call, is what actually exercises
    Task 10's new behaviour: catching that error gracefully and killing the
    orphaned box session it left running rather than leaving it to burn
    tokens unsupervised.
    """
    from eve.coding import store
    from eve.memory import db as memory_db

    _post(client, _created())
    time.sleep(0.5)
    _post(client, _created())
    time.sleep(0.5)

    row = await store.get_by_linear_session("lin_sess_1")
    assert row is not None
    pool = await memory_db.get_pool()
    async with pool.connection() as conn:
        result = await conn.execute(
            "SELECT count(*) FROM eve_coding_session WHERE linear_session_id = %s",
            ("lin_sess_1",),
        )
        assert (await result.fetchone())[0] == 1

    # Load-bearing for Task 10 specifically: the retry's orphaned box
    # session (created before the duplicate row write failed) must be
    # killed, not left running unsupervised.
    assert len(world["killed"]) == 1
    assert world["killed"]
