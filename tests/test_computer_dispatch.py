from unittest.mock import AsyncMock

import pytest

from eve.computer import dispatch


def _state(permissions=("computer.use",)):
    return {
        "messages": [],
        "member": {
            "sub": "sub-noah",
            "name": "Noah",
            "role": "user",
            "timezone": "America/Los_Angeles",
            "permissions": list(permissions),
            "local_time": "2024-01-01T12:00:00",
        },
        "system_prompt": "You are Eve, a family AI assistant.",
        "memory": None,
        "dynamic_tools": [],
        "suggestions": [],
    }


def _config(thread_id="thread-1"):
    return {"configurable": {"thread_id": thread_id}}


async def test_a_member_without_the_permission_is_denied(monkeypatch):
    dispatch_task = AsyncMock()
    monkeypatch.setattr(dispatch, "dispatch_task", dispatch_task)

    result = await dispatch.dispatch_computer_task.ainvoke(
        {"goal": "book a flight", "state": _state(permissions=())}, config=_config()
    )

    assert "Permission denied" in result
    assert "computer.use" in result
    dispatch_task.assert_not_awaited()


async def test_a_permitted_member_dispatches_and_records_the_task(monkeypatch):
    monkeypatch.setattr(dispatch, "dispatch_task", AsyncMock(return_value="ok"))
    create_task = AsyncMock()
    monkeypatch.setattr(dispatch, "create_task", create_task)

    result = await dispatch.dispatch_computer_task.ainvoke(
        {"goal": "book a flight", "state": _state()}, config=_config()
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
        {"goal": "book a flight", "state": _state()}, config=_config()
    )

    assert result.startswith("error:")
    create_task.assert_not_awaited()


async def test_no_thread_id_is_refused_before_dispatching(monkeypatch):
    dispatch_task = AsyncMock()
    monkeypatch.setattr(dispatch, "dispatch_task", dispatch_task)

    result = await dispatch.dispatch_computer_task.ainvoke(
        {"goal": "book a flight", "state": _state()}, config=_config(thread_id=None)
    )

    assert result.startswith("error:")
    dispatch_task.assert_not_awaited()
