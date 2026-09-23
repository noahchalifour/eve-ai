import pytest

from eve_linear import handler
from eve_linear.types import LinearEvent


@pytest.fixture(autouse=True)
def roster_and_settings(tmp_path, monkeypatch):
    path = tmp_path / "family.yaml"
    path.write_text(
        "members:\n"
        "  - sub: 'noah-sub'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    linear_id: 'lin_noah'\n"
        "    permissions: ['code.delegate']\n"
        "  - sub: 'kendra-sub'\n"
        "    name: 'Kendra'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    linear_id: 'lin_kendra'\n"
        "    permissions: []\n"
    )
    monkeypatch.setenv("EVE_FAMILY_FILE", str(path))
    monkeypatch.setenv("EVE_LINEAR_ENABLED", "true")
    monkeypatch.setenv("EVE_LINEAR_WEBHOOK_SECRET", "s" * 32)
    monkeypatch.setenv("EVE_LINEAR_REPO_ALLOWLIST", '["owner/repo"]')
    from eve.family import get_family
    from eve.settings import get_settings

    get_settings.cache_clear()
    get_family.cache_clear()
    yield
    get_settings.cache_clear()
    get_family.cache_clear()


def _event(**overrides) -> LinearEvent:
    base = {
        "action": "created",
        "session_id": "lin_sess_1",
        "issue_id": "lin_issue_1",
        "team_id": "lin_team_1",
        "actor_id": "lin_noah",
        "guidance": "Work in owner/repo.",
        "prompt_body": "",
        "prompt_context": "Fix the login bug.",
    }
    base.update(overrides)
    return LinearEvent(**base)


@pytest.fixture
def spy(monkeypatch):
    """Records every outbound effect, in order, so a test can assert on
    ordering rather than only on occurrence."""
    calls = []

    async def _emit(linear_session_id, content, session_id=None):
        calls.append(("emit", content["type"]))
        return True

    async def _recall(goal, member_sub):
        calls.append(("recall", goal))
        return "remembered things"

    async def _create(session_id, agent, model, repos, goal):
        calls.append(("create_coding_session", repos))
        return "ok"

    async def _store_create(**kwargs):
        calls.append(("row", kwargs.get("linear_session_id")))

    async def _thread():
        calls.append(("thread", None))
        return "thread-1"

    async def _move(issue_id, team_id):
        calls.append(("move_issue", issue_id))
        return {"success": True}

    async def _validate(model, agent):
        # `model` is NOT NULL on the row and nobody in Linear names one, so
        # the handler always resolves the agent's fallback through here.
        return "claude-sonnet-5"

    monkeypatch.setattr(handler.activities, "emit", _emit)
    monkeypatch.setattr(handler, "_recall_context", _recall)
    monkeypatch.setattr(handler, "create_coding_session", _create)
    monkeypatch.setattr(handler.store, "create_session", _store_create)
    monkeypatch.setattr(handler, "_create_thread", _thread)
    monkeypatch.setattr(handler, "_move_issue_to_started", _move)
    monkeypatch.setattr(handler.catalogue, "validate", _validate)
    return calls


def test_parse_event_reads_the_payload_linear_actually_sends():
    payload = {
        "action": "created",
        "agentSession": {
            "id": "lin_sess_1",
            "issue": {"id": "lin_issue_1", "team": {"id": "lin_team_1"}},
            "creator": {"id": "lin_noah"},
            "guidance": "Work in owner/repo.",
            "promptContext": "Fix the login bug.",
        },
    }
    event = handler.parse_event(payload)
    assert event.action == "created"
    assert event.session_id == "lin_sess_1"
    assert event.issue_id == "lin_issue_1"
    assert event.team_id == "lin_team_1"
    assert event.actor_id == "lin_noah"
    assert "owner/repo" in event.guidance


def test_parse_event_survives_a_payload_with_no_issue():
    # A mention in a document has no issue. Eve refuses it later, but parsing
    # must not raise, or the 202 path turns into a 500 and Linear retries.
    event = handler.parse_event({"action": "created", "agentSession": {"id": "s"}})
    assert event.issue_id is None


async def test_the_acknowledgement_is_emitted_before_recall(spy):
    # THE CANARY. This ordering is the entire 10-second contract, and it is
    # invisible in the code once written. A refactor that hoists a database
    # read above the emission breaks the contract while every other test
    # still passes.
    await handler.handle_created(_event())

    kinds = [c[0] for c in spy]
    assert kinds.index("emit") < kinds.index("recall")
    assert spy[0] == ("emit", "thought")


async def test_an_accepted_delegation_dispatches_and_writes_the_row(spy):
    result = await handler.handle_created(_event())
    assert result == "dispatched"
    assert ("create_coding_session", ["owner/repo"]) in spy
    assert ("row", "lin_sess_1") in spy


async def test_an_unmapped_linear_user_is_refused_with_an_error(spy):
    result = await handler.handle_created(_event(actor_id="lin_stranger"))
    assert result == "refused:unmapped"
    assert spy[0] == ("emit", "error")
    assert not any(c[0] == "recall" for c in spy)
    assert not any(c[0] == "create_coding_session" for c in spy)


async def test_a_member_without_the_permission_is_refused_with_an_error(spy):
    result = await handler.handle_created(_event(actor_id="lin_kendra"))
    assert result == "refused:unpermitted"
    assert spy[0] == ("emit", "error")
    assert not any(c[0] == "create_coding_session" for c in spy)


async def test_guidance_outside_the_allowlist_is_refused_with_an_elicitation(spy):
    result = await handler.handle_created(_event(guidance="Use evil/backdoor."))
    assert result == "refused:no_repo"
    assert spy[0] == ("emit", "elicitation")
    assert not any(c[0] == "create_coding_session" for c in spy)


async def test_a_dispatch_failure_after_the_acknowledgement_emits_an_error(
    spy, monkeypatch
):
    async def _failing_create(session_id, agent, model, repos, goal):
        return "error: eve-computer unavailable (ConnectError)"

    monkeypatch.setattr(handler, "create_coding_session", _failing_create)

    result = await handler.handle_created(_event())

    assert result == "failed"
    assert [c[1] for c in spy if c[0] == "emit"] == ["thought", "error"]
    # No row, or the supervisor polls forever for work that never started.
    assert not any(c[0] == "row" for c in spy)


async def test_a_prompted_event_resumes_a_blocked_session(monkeypatch):
    sent = []

    async def _get(linear_session_id):
        return {"id": "row-1", "status": "blocked", "linear_session_id": linear_session_id}

    async def _prompt(session_id, text, kind="reply"):
        sent.append((session_id, text, kind))
        return "ok"

    async def _set_status(session_id, status):
        sent.append(("status", session_id, status))

    monkeypatch.setattr(handler.store, "get_by_linear_session", _get)
    monkeypatch.setattr(handler.store, "set_status", _set_status)
    monkeypatch.setattr(handler, "prompt_coding_session", _prompt)

    result = await handler.handle_prompted(
        _event(action="prompted", prompt_body="Use the staging database.")
    )

    assert result == "resumed"
    assert ("row-1", "Use the staging database.", "interjection") in sent
    assert ("status", "row-1", "running") in sent


async def test_a_prompted_event_for_a_finished_session_says_so(monkeypatch):
    emitted = []

    async def _get(linear_session_id):
        return {"id": "row-1", "status": "finished"}

    async def _emit(linear_session_id, content, session_id=None):
        emitted.append(content["type"])
        return True

    monkeypatch.setattr(handler.store, "get_by_linear_session", _get)
    monkeypatch.setattr(handler.activities, "emit", _emit)

    result = await handler.handle_prompted(_event(action="prompted", prompt_body="hi"))
    assert result == "already-resolved"
    assert emitted == ["error"]


async def test_a_prompted_event_for_an_unknown_session_is_reported(monkeypatch):
    async def _get(linear_session_id):
        return None

    monkeypatch.setattr(handler.store, "get_by_linear_session", _get)

    result = await handler.handle_prompted(_event(action="prompted", prompt_body="hi"))
    assert result == "unknown-session"


async def test_a_thread_creation_failure_degrades_to_none_rather_than_raising(
    monkeypatch,
):
    # `_create_thread` imports `get_client` locally (inside its own function
    # body), so there is no module-level `handler.get_client` to patch -
    # patching `langgraph_sdk.get_client` is what the local import actually
    # resolves at call time.
    class _RaisingClient:
        class threads:
            @staticmethod
            async def create(metadata):
                raise RuntimeError("aegra unreachable")

    monkeypatch.setattr("langgraph_sdk.get_client", lambda **kwargs: _RaisingClient())

    result = await handler._create_thread()
    assert result is None


def _real_payload() -> dict:
    """The shape Linear actually sends, captured from a live delivery.

    Kept as a fixture rather than inlined because the nesting is the whole
    point: `guidance` and `promptContext` are TOP-LEVEL, not inside
    `agentSession`, and `guidance` is a list of origin/body objects rather
    than a string. All three were wrong in the original implementation.
    """
    import json
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / "linear_agent_session_created.json"
    return json.loads(path.read_text())


def test_parse_event_reads_top_level_guidance_as_text():
    """Linear sends `guidance` as a list of {origin, body} objects at the top
    level of the payload. `resolve_repos` runs a regex over it, so a list
    raises TypeError and the delegation dies with no activity emitted."""
    event = handler.parse_event(_real_payload())
    assert isinstance(event.guidance, str)
    assert "owner/repo" in event.guidance
    assert "owner/client-app" in event.guidance


def test_parse_event_reads_top_level_prompt_context():
    """`promptContext` is top-level too, not inside `agentSession`. Reading
    it from the wrong place made the goal silently empty."""
    event = handler.parse_event(_real_payload())
    assert "EVE-1" in event.prompt_context


def test_parse_event_still_reads_the_session_fields():
    event = handler.parse_event(_real_payload())
    assert event.action == "created"
    assert event.session_id == "lin_sess_real"
    assert event.issue_id == "lin_issue_1"
    assert event.team_id == "lin_team_1"
    assert event.actor_id == "lin_noah"


def test_guidance_survives_every_shape_linear_might_send():
    """A string, a list of objects, a list of bare strings, None, and a
    malformed entry all have to produce a string, because the alternative is
    a TypeError inside a background task that emits nothing at all."""
    for raw, expected in (
        ("plain string owner/repo", "owner/repo"),
        ([{"body": "owner/repo"}], "owner/repo"),
        (["owner/repo"], "owner/repo"),
        ([{"origin": {}, "body": "a"}, {"body": "owner/repo"}], "owner/repo"),
        (None, ""),
        ([], ""),
        ([{"no_body_key": "x"}], ""),
    ):
        event = handler.parse_event({"action": "created", "guidance": raw,
                                     "agentSession": {"id": "s"}})
        assert isinstance(event.guidance, str)
        if expected:
            assert expected in event.guidance
