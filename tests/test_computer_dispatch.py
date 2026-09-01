from unittest.mock import AsyncMock

import pytest

from eve.computer import dispatch


def _config(permissions=("computer.use",), thread_id="thread-1"):
    return {
        "configurable": {
            "member": {"sub": "sub-noah", "permissions": list(permissions)},
            "thread_id": thread_id,
        }
    }


async def test_a_member_without_the_permission_is_denied(monkeypatch):
    dispatch_task = AsyncMock()
    monkeypatch.setattr(dispatch, "dispatch_task", dispatch_task)

    result = await dispatch.dispatch_computer_task.ainvoke(
        {"goal": "book a flight"}, config=_config(permissions=())
    )

    assert "Permission denied" in result
    assert "computer.use" in result
    dispatch_task.assert_not_awaited()


async def test_a_permitted_member_dispatches_and_records_the_task(monkeypatch):
    monkeypatch.setattr(dispatch, "dispatch_task", AsyncMock(return_value="ok"))
    create_task = AsyncMock()
    monkeypatch.setattr(dispatch, "create_task", create_task)

    result = await dispatch.dispatch_computer_task.ainvoke(
        {"goal": "book a flight"}, config=_config()
    )

    assert "I'm on it" in result
    create_task.assert_awaited_once()
    kwargs = create_task.await_args.kwargs
    assert kwargs["member_sub"] == "sub-noah"
    assert kwargs["thread_id"] == "thread-1"
    assert kwargs["goal"] == "book a flight"
    assert isinstance(kwargs["task_id"], str) and kwargs["task_id"]


async def test_a_dispatch_failure_is_returned_and_nothing_is_recorded(monkeypatch):
    monkeypatch.setattr(
        dispatch, "dispatch_task", AsyncMock(return_value="error: eve-computer unavailable")
    )
    create_task = AsyncMock()
    monkeypatch.setattr(dispatch, "create_task", create_task)

    result = await dispatch.dispatch_computer_task.ainvoke(
        {"goal": "book a flight"}, config=_config()
    )

    assert result.startswith("error:")
    create_task.assert_not_awaited()


async def test_no_thread_id_is_refused_before_dispatching(monkeypatch):
    dispatch_task = AsyncMock()
    monkeypatch.setattr(dispatch, "dispatch_task", dispatch_task)

    result = await dispatch.dispatch_computer_task.ainvoke(
        {"goal": "book a flight"}, config=_config(thread_id=None)
    )

    assert result.startswith("error:")
    dispatch_task.assert_not_awaited()
