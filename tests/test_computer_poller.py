from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from eve.computer import poller


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_TASK_STALE_MINUTES", "60")
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _task(task_id="t1", updated_at=None, member_sub="sub-noah", thread_id="thread-1", goal="do it"):
    return {
        "id": task_id, "member_sub": member_sub, "thread_id": thread_id,
        "goal": goal, "status": "running", "result": None,
        "updated_at": updated_at or datetime.now(UTC),
    }


async def test_a_task_the_box_reports_finished_is_marked_finished(monkeypatch):
    monkeypatch.setattr(poller.store, "running_tasks", AsyncMock(return_value=[_task()]))
    mark_finished = AsyncMock()
    monkeypatch.setattr(poller.store, "mark_finished", mark_finished)
    monkeypatch.setattr(
        poller.tools_client, "get_computer_task",
        AsyncMock(return_value={"status": "finished", "result": {"summary": "done"}}),
    )

    resolved = await poller.sync(now=datetime.now(UTC))

    mark_finished.assert_awaited_once_with("t1", "finished", {"summary": "done"})
    assert resolved[0]["status"] == "finished"
    assert resolved[0]["result"] == {"summary": "done"}


async def test_a_result_carrying_an_error_is_marked_failed_even_if_the_box_said_finished(
    monkeypatch,
):
    monkeypatch.setattr(poller.store, "running_tasks", AsyncMock(return_value=[_task()]))
    mark_finished = AsyncMock()
    monkeypatch.setattr(poller.store, "mark_finished", mark_finished)
    monkeypatch.setattr(
        poller.tools_client, "get_computer_task",
        AsyncMock(return_value={"status": "finished", "result": {"error": "boom"}}),
    )

    resolved = await poller.sync(now=datetime.now(UTC))

    mark_finished.assert_awaited_once_with("t1", "failed", {"error": "boom"})
    assert resolved[0]["status"] == "failed"


async def test_the_box_reporting_failed_is_marked_failed(monkeypatch):
    monkeypatch.setattr(poller.store, "running_tasks", AsyncMock(return_value=[_task()]))
    mark_finished = AsyncMock()
    monkeypatch.setattr(poller.store, "mark_finished", mark_finished)
    monkeypatch.setattr(
        poller.tools_client, "get_computer_task",
        AsyncMock(return_value={"status": "failed", "result": {"error": "killed"}}),
    )

    await poller.sync(now=datetime.now(UTC))
    mark_finished.assert_awaited_once_with("t1", "failed", {"error": "killed"})


async def test_a_still_running_task_is_left_alone(monkeypatch):
    monkeypatch.setattr(poller.store, "running_tasks", AsyncMock(return_value=[_task()]))
    mark_finished = AsyncMock()
    mark_stale = AsyncMock()
    monkeypatch.setattr(poller.store, "mark_finished", mark_finished)
    monkeypatch.setattr(poller.store, "mark_stale", mark_stale)
    monkeypatch.setattr(
        poller.tools_client, "get_computer_task",
        AsyncMock(return_value={"status": "running", "result": None}),
    )

    resolved = await poller.sync(now=datetime.now(UTC))

    mark_finished.assert_not_awaited()
    mark_stale.assert_not_awaited()
    assert resolved == []


async def test_an_unreachable_box_within_the_stale_window_is_left_alone(monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setattr(
        poller.store, "running_tasks",
        AsyncMock(return_value=[_task(updated_at=now - timedelta(minutes=10))]),
    )
    mark_stale = AsyncMock()
    monkeypatch.setattr(poller.store, "mark_stale", mark_stale)
    monkeypatch.setattr(poller.tools_client, "get_computer_task", AsyncMock(return_value=None))

    resolved = await poller.sync(now=now)

    mark_stale.assert_not_awaited()
    assert resolved == []


async def test_an_unreachable_box_past_the_stale_window_is_marked_stale(monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setattr(
        poller.store, "running_tasks",
        AsyncMock(return_value=[_task(updated_at=now - timedelta(minutes=61))]),
    )
    mark_stale = AsyncMock()
    monkeypatch.setattr(poller.store, "mark_stale", mark_stale)
    monkeypatch.setattr(poller.tools_client, "get_computer_task", AsyncMock(return_value=None))

    resolved = await poller.sync(now=now)

    mark_stale.assert_awaited_once_with("t1")
    assert resolved[0]["status"] == "stale"


async def test_one_tasks_failure_does_not_stop_the_rest_from_being_checked(monkeypatch):
    tasks = [_task("t1"), _task("t2")]
    monkeypatch.setattr(poller.store, "running_tasks", AsyncMock(return_value=tasks))
    monkeypatch.setattr(poller.store, "mark_finished", AsyncMock())

    async def _status(task_id):
        if task_id == "t1":
            raise RuntimeError("transient")
        return {"status": "finished", "result": {"summary": "ok"}}

    monkeypatch.setattr(poller.tools_client, "get_computer_task", AsyncMock(side_effect=_status))

    resolved = await poller.sync(now=datetime.now(UTC))
    assert [row["id"] for row in resolved] == ["t2"]
