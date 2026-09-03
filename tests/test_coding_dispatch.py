"""Mirrors tests/test_computer_dispatch.py. The load-bearing test in this
file is the first one: a denied member's request must never reach the box,
which is ADR 0006's whole pattern."""

from unittest.mock import AsyncMock

import pytest

from eve.coding import dispatch


def _config(permissions=("code.delegate",), sub="sub-noah", thread="t1"):
    return {"configurable": {"member": {"sub": sub, "permissions": list(permissions)},
                             "thread_id": thread}}


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(dispatch, "create_coding_session", AsyncMock(return_value="ok"))
    monkeypatch.setattr(dispatch.store, "create_session", AsyncMock())
    monkeypatch.setattr(dispatch.store, "live_sessions_for", AsyncMock(return_value=[]))
    monkeypatch.setattr(dispatch.catalogue, "validate", AsyncMock(return_value="chatgpt/gpt-5.6-sol"))
    monkeypatch.setattr(dispatch, "_recall_context", AsyncMock(return_value="ctx"))
    monkeypatch.setattr(dispatch, "prompt_coding_session", AsyncMock(return_value="ok"))


async def test_a_member_without_the_permission_never_reaches_the_box():
    result = await dispatch.delegate_coding_task.ainvoke(
        {"repos": ["acme/repo"], "goal": "fix it"}, config=_config(permissions=[])
    )

    assert "Permission denied" in result
    dispatch.create_coding_session.assert_not_awaited()


async def test_dispatch_returns_immediately_and_records_the_session():
    result = await dispatch.delegate_coding_task.ainvoke(
        {"repos": ["acme/repo"], "goal": "fix it"}, config=_config()
    )

    assert "on it" in result.lower()
    dispatch.store.create_session.assert_awaited_once()
    kwargs = dispatch.store.create_session.await_args.kwargs
    assert kwargs["member_sub"] == "sub-noah"
    assert kwargs["repos"] == ["acme/repo"]
    assert kwargs["context"] == "ctx"


async def test_the_agent_falls_back_to_the_configured_tiebreak(monkeypatch):
    monkeypatch.setenv("EVE_CODING_DEFAULT_AGENT", "codex")
    from eve.settings import get_settings

    get_settings.cache_clear()

    await dispatch.delegate_coding_task.ainvoke(
        {"repos": ["acme/repo"], "goal": "fix it"}, config=_config()
    )

    assert dispatch.store.create_session.await_args.kwargs["agent"] == "codex"
    get_settings.cache_clear()


async def test_an_agent_eve_names_is_honoured():
    await dispatch.delegate_coding_task.ainvoke(
        {"repos": ["acme/repo"], "goal": "fix it", "agent": "claude"}, config=_config()
    )

    assert dispatch.store.create_session.await_args.kwargs["agent"] == "claude"


async def test_an_unknown_agent_is_refused_before_the_http_call():
    result = await dispatch.delegate_coding_task.ainvoke(
        {"repos": ["acme/repo"], "goal": "fix it", "agent": "cursor"}, config=_config()
    )

    assert "cursor" in result
    dispatch.create_coding_session.assert_not_awaited()


async def test_the_model_is_validated_before_dispatch():
    await dispatch.delegate_coding_task.ainvoke(
        {"repos": ["acme/repo"], "goal": "fix it", "model": "gpt-9-ultra"}, config=_config()
    )

    dispatch.catalogue.validate.assert_awaited_once_with("gpt-9-ultra", "codex")
    assert dispatch.store.create_session.await_args.kwargs["model"] == "chatgpt/gpt-5.6-sol"


async def test_no_repos_is_refused():
    result = await dispatch.delegate_coding_task.ainvoke(
        {"repos": [], "goal": "fix it"}, config=_config()
    )

    assert "repo" in result.lower()
    dispatch.create_coding_session.assert_not_awaited()


async def test_a_box_failure_leaves_no_orphan_row():
    dispatch.create_coding_session.return_value = "error: eve-computer unavailable"

    result = await dispatch.delegate_coding_task.ainvoke(
        {"repos": ["acme/repo"], "goal": "fix it"}, config=_config()
    )

    assert result.startswith("error:")
    dispatch.store.create_session.assert_not_awaited()


async def test_check_lists_only_the_asking_members_sessions(monkeypatch):
    dispatch.store.live_sessions_for.return_value = [
        {"id": "abcdef12-3456", "repos": ["acme/repo"], "goal": "fix it",
         "status": "running", "agent": "codex", "model": "m"}
    ]
    monkeypatched = AsyncMock(return_value={"activity": ["tool: edit README"]})
    dispatch.get_coding_session = monkeypatched

    result = await dispatch.check_coding_session.ainvoke({}, config=_config())

    dispatch.store.live_sessions_for.assert_awaited_once_with("sub-noah")
    assert "abcdef12" in result
    assert "edit README" in result


async def test_check_denies_a_member_without_the_permission():
    result = await dispatch.check_coding_session.ainvoke({}, config=_config(permissions=[]))

    assert "Permission denied" in result


async def test_an_interjection_is_sent_as_an_interjection():
    monkeypatch_get = AsyncMock(return_value={"id": "s1", "member_sub": "sub-noah"})
    dispatch.store.get = monkeypatch_get

    result = await dispatch.send_to_coding_session.ainvoke(
        {"session_id": "s1", "message": "use httpx"}, config=_config()
    )

    dispatch.prompt_coding_session.assert_awaited_once_with("s1", "use httpx", kind="interjection")
    assert "pass that on" in result.lower() or "told" in result.lower()


async def test_a_member_cannot_interject_into_another_members_session():
    dispatch.store.get = AsyncMock(return_value={"id": "s1", "member_sub": "sub-kendra"})

    result = await dispatch.send_to_coding_session.ainvoke(
        {"session_id": "s1", "message": "use httpx"}, config=_config(sub="sub-noah")
    )

    assert "don't have a session" in result.lower() or "not yours" in result.lower()
    dispatch.prompt_coding_session.assert_not_awaited()
