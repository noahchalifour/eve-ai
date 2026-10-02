"""Reminders (ENG-372): one-shot routines delivered verbatim.

Ambient turns are built with the real marker (see test_routines_tools.py's
note on why a hand-rolled config key is not a test of the guard).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from eve.general import reminders
from eve.routines import cadence
from eve.state import ambient_marker

MEMBER = {
    "sub": "sub-noah", "name": "Noah", "role": "adult", "timezone": "America/Los_Angeles",
    "permissions": ["routines"], "local_time": "2026-10-07 14:00 PDT",
}
CONFIG = {"configurable": {"member": MEMBER}}


def _state(messages):
    return {"messages": messages, "member": MEMBER, "system_prompt": "", "memory": None,
            "dynamic_tools": [], "suggestions": []}


TYPED = _state([HumanMessage("Remind me in 20 minutes to take the laundry out")])
AMBIENT = _state([HumanMessage(ambient_marker("Noah") + "\nA routine fired.")])
READ_WEB = _state([
    HumanMessage("read this and remind me"),
    AIMessage("", tool_calls=[{"name": "fetch_url", "args": {"url": "https://x"}, "id": "c1"}]),
])


async def _call(tool, args, state=TYPED, config=CONFIG):
    return await tool.ainvoke({"type": "tool_call", "name": tool.name, "id": "t1",
                               "args": {**args, "state": state}}, config)


@pytest.fixture
def created(monkeypatch):
    rows: list[dict] = []

    async def fake_create(**kwargs):
        rows.append(kwargs)
        return {**kwargs, "id": "r-1"}

    monkeypatch.setattr(reminders.store, "create", fake_create)
    return rows


async def test_sets_a_one_shot_reminder_in_local_time(created):
    out = await _call(reminders.set_reminder, {"message": "Take the laundry out", "at": "in 20 minutes"})
    assert "Reminder set for" in out.content
    row = created[0]
    assert row["kind"] == "reminder"
    assert row["instruction"] == "Take the laundry out"
    moment = datetime.fromisoformat(row["cadence"]["once_at"])
    assert moment.tzinfo is not None
    assert abs((moment - datetime.now(UTC)) - timedelta(minutes=20)) < timedelta(seconds=5)
    assert row["next_run_at"] == moment.astimezone(UTC)


async def test_refuses_ambient_web_past_and_unparseable(created):
    assert "Only Noah can" in (await _call(reminders.set_reminder, {"message": "x", "at": "in 5 minutes"}, AMBIENT)).content
    assert "web" in (await _call(reminders.set_reminder, {"message": "x", "at": "in 5 minutes"}, READ_WEB)).content
    assert "past" in (await _call(reminders.set_reminder, {"message": "x", "at": "2001-01-01T00:00"})).content
    assert "rejected" in (await _call(reminders.set_reminder, {"message": "x", "at": "whenever"})).content
    assert "366 days" in (await _call(reminders.set_reminder, {"message": "x", "at": "in 400 days"})).content
    assert created == []


async def test_needs_the_routines_grant(created):
    config = {"configurable": {"member": {**MEMBER, "permissions": []}}}
    out = await _call(reminders.set_reminder, {"message": "x", "at": "in 5 minutes"}, config=config)
    assert out.content.startswith("Permission denied")
    assert created == []


async def test_list_and_cancel_only_see_reminders(monkeypatch):
    soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    row = {"id": "r-1", "title": "Call mom", "instruction": "Call mom", "status": "active",
           "cadence": {"once_at": soon}}
    kinds = []

    async def fake_list(sub, kind=None):
        kinds.append(kind)
        return [row]

    async def fake_find(sub, text, kind=None):
        kinds.append(kind)
        return [row] if text.lower() in "call mom" else []

    async def fake_delete(sub, rid):
        return True

    monkeypatch.setattr(reminders.store, "list_for", fake_list)
    monkeypatch.setattr(reminders.store, "find_by_title", fake_find)
    monkeypatch.setattr(reminders.store, "delete", fake_delete)
    assert "Call mom" in (await _call(reminders.list_reminders, {})).content
    assert "Cancelled" in (await _call(reminders.cancel_reminder, {"reference": "mom"})).content
    assert "No upcoming" in (await _call(reminders.cancel_reminder, {"reference": "dentist"})).content
    assert set(kinds) == {"reminder"}


# --- cadence -----------------------------------------------------------


def test_once_at_validation():
    now = datetime(2026, 10, 7, tzinfo=UTC)
    assert cadence.validate({"once_at": "2026-10-08T09:00:00-07:00"}) is None
    assert "offset" in cadence.validate({"once_at": "2026-10-08T09:00"})
    assert "exactly one" in cadence.validate({"once_at": "2026-10-08T09:00:00Z", "daily_at": "08:00"})
    assert "past" in cadence.validate_at({"once_at": "2026-10-06T09:00:00+00:00"}, now)
    assert cadence.validate_at({"once_at": "2026-10-08T09:00:00+00:00"}, now) is None
    assert cadence.next_after({"once_at": "2026-10-08T09:00:00-07:00"}, "UTC", now) == datetime(2026, 10, 8, 16, tzinfo=UTC)
    assert cadence.describe({"once_at": "2026-10-08T09:00:00-07:00"}).startswith("once")


async def test_schedule_routine_sends_one_offs_to_set_reminder(monkeypatch):
    from eve.routines import tools

    out = await _call(tools.schedule_routine, {
        "title": "x", "instruction": "x", "cadence": {"once_at": "2030-01-01T00:00:00+00:00"},
    })
    assert "set_reminder" in out.content


async def test_schedule_routine_refuses_after_reading_the_web():
    from eve.routines import tools

    out = await _call(tools.schedule_routine, {
        "title": "x", "instruction": "x", "cadence": {"daily_at": "08:00"},
    }, READ_WEB)
    assert "web" in out.content
