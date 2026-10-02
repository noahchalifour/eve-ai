"""A reminder firing (ENG-372): claimed like a routine, pushed verbatim with
no thread and no model call, then retired - and retried, not lost, when the
push fails."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from eve_ambient import notify, pipeline
from eve_ambient.sources import routines
from eve_ambient.types import FilterVerdict, Signal

ONCE = {"once_at": "2026-10-07T14:20:00-07:00"}
DUE = {
    "id": "r-9", "member_sub": "sub-noah", "title": "Take the laundry out",
    "instruction": "Take the laundry out", "cadence": ONCE, "timezone": "America/Los_Angeles",
    "status": "active", "kind": "reminder",
    "scheduled_for": datetime(2026, 10, 7, 21, 20, tzinfo=UTC),
    "last_run_at": None, "expires_at": None, "created_at": datetime(2026, 10, 7, tzinfo=UTC),
}


@pytest.fixture(autouse=True)
def _store(monkeypatch):
    monkeypatch.setattr(routines.store, "claim_due", AsyncMock(return_value=[DUE]))
    monkeypatch.setattr(routines.store, "schedule_next", AsyncMock())
    monkeypatch.setattr(routines.store, "record_run", AsyncMock())
    monkeypatch.setattr(routines.store, "expire", AsyncMock())


class Notifier:
    def __init__(self, result=True):
        self.result, self.calls = result, []

    async def send(self, *, title, body, urgent, click_url):
        self.calls.append({"title": title, "body": body, "urgent": urgent, "click_url": click_url})
        return self.result


async def test_a_one_shot_is_not_rescheduled_and_carries_its_kind():
    signals = await routines.poll("")
    assert signals[0].payload["kind"] == "reminder"
    routines.store.schedule_next.assert_not_awaited()


async def test_delivery_pushes_the_message_verbatim_without_a_turn(monkeypatch):
    monkeypatch.setattr(notify, "get_client", lambda **_kw: pytest.fail("no Aegra client for a reminder"))
    [signal] = await routines.poll("")
    notifier = Notifier()
    verdict = FilterVerdict(notify=True, audience=["sub-noah"], urgent=False, why="asked")
    assert await notify.deliver(signal, None, verdict, notifier) == ""
    assert notifier.calls == [{"title": "Eve - reminder", "body": "Take the laundry out",
                               "urgent": False, "click_url": None}]


async def test_a_failed_push_is_a_delivery_error_so_the_lease_retries_it():
    [signal] = await routines.poll("")
    verdict = FilterVerdict(notify=True, audience=["sub-noah"], urgent=False, why="asked")
    with pytest.raises(notify.DeliveryError):
        await notify.deliver(signal, None, verdict, Notifier(result=False))


async def test_sent_retires_the_reminder():
    [signal] = await routines.poll("")
    await routines.record_outcome(signal, "sent")
    routines.store.record_run.assert_awaited_once()
    routines.store.expire.assert_awaited_once_with("r-9")


async def test_deferred_keeps_it_for_the_retry():
    [signal] = await routines.poll("")
    await routines.record_outcome(signal, "deferred")
    routines.store.expire.assert_not_awaited()


async def test_an_unpermitted_reminder_is_retired_rather_than_left_leased():
    [signal] = await routines.poll("")
    await routines.record_outcome(signal, "unpermitted")
    routines.store.expire.assert_awaited_once_with("r-9")


async def test_a_recurring_routine_is_never_expired_by_an_outcome():
    signal = Signal(source="routines", key="r-1:x", occurred_at=datetime.now(UTC),
                    member_sub="sub-noah", summary="", payload={"routine_id": "r-1",
                    "cadence": {"daily_at": "08:00"}})
    await routines.record_outcome(signal, "sent")
    await routines.record_outcome(signal, "unpermitted")
    routines.store.expire.assert_not_awaited()


async def test_the_pipeline_delivers_and_records_a_notice_without_a_thread(monkeypatch):
    from eve.settings import get_settings

    get_settings.cache_clear()
    [signal] = await routines.poll("")
    recorded = AsyncMock()
    monkeypatch.setattr(pipeline.store, "is_fresh", AsyncMock(return_value=True))
    monkeypatch.setattr(pipeline.store, "already_notified", AsyncMock(return_value=False))
    monkeypatch.setattr(pipeline.store, "mark_seen", AsyncMock())
    monkeypatch.setattr(pipeline.store, "record_notice", recorded)

    class _Family:
        def get(self, sub):
            from eve.family import Member

            return Member(sub=sub, name="Noah", role="adult", timezone="America/Los_Angeles",
                          permissions=frozenset({"routines"}))

    monkeypatch.setattr(pipeline, "get_family", lambda: _Family())
    monkeypatch.setattr(pipeline.gates, "get_family", lambda: _Family())
    notifier = Notifier()
    outcome = await pipeline.handle_signal(signal, notifier=notifier)
    assert outcome == "sent"
    assert notifier.calls[0]["body"] == "Take the laundry out"
    assert recorded.await_args.args[-1] is None
    routines.store.expire.assert_awaited_once_with("r-9")
