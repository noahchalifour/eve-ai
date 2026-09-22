"""These three tools are the only way a routine is created or destroyed, so
every guard that matters is here.

Note how the ambient tests below build a real marked message rather than
setting a config key: a test that invents its own marker of ambience can pass
against a guard production never triggers, which is exactly how
save_widget's broken `is_ambient` check survived review (EVE-30).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from langchain_core.messages import HumanMessage

from eve.state import ambient_marker

CADENCE = {"daily_at": "08:00"}

MEMBER = {
    "sub": "sub-noah",
    "name": "Noah",
    "role": "adult",
    "timezone": "America/Vancouver",
    "permissions": ["routines"],
    "local_time": "2026-01-11 08:00 PST",
}

CONFIG = {"configurable": {"member": MEMBER}}
NO_PERMS = {
    "configurable": {"member": {**MEMBER, "permissions": []}}
}

# InjectedState validates the whole EveState/MemberContext shape strictly
# when a tool is invoked directly with .ainvoke (unlike a plain TypedDict at
# runtime) - see tests/test_specialists_base.py, test_skills_search.py,
# test_skills_authoring.py, test_tools_propose.py and test_memory_search.py
# for the same full-state convention.
def _full_state(messages):
    return {
        "messages": messages,
        "member": MEMBER,
        "system_prompt": "",
        "memory": None,
        "dynamic_tools": [],
        "suggestions": [],
    }


TYPED = _full_state(
    [HumanMessage(content="Track Aeroplan fares to Los Cabos.")]
)
AMBIENT = _full_state(
    [HumanMessage(content=ambient_marker("Noah") + "\nA routine fired.")]
)

ROW = {
    "id": "r-1",
    "member_sub": "sub-noah",
    "title": "Flights",
    "instruction": "Check fares.",
    "cadence": CADENCE,
    "timezone": "America/Vancouver",
    "status": "active",
    "next_run_at": datetime(2026, 1, 11, 16, 0, tzinfo=UTC),
    "last_run_at": None,
    "last_outcome": None,
    "consecutive_failures": 0,
    "expires_at": None,
    "revision": 1,
}


def _call(tool, args, config=CONFIG, state=TYPED):
    return tool.ainvoke(
        {
            "type": "tool_call",
            "name": tool.name,
            "args": {**args, "state": state},
            "id": "t1",
        },
        config=config,
    )


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setenv("EVE_ROUTINES_ENABLED", "true")


async def test_scheduling_stores_a_routine_for_the_authenticated_member(monkeypatch):
    from eve.routines import tools

    seen = {}

    async def fake_create(**kwargs):
        seen.update(kwargs)
        return ROW

    monkeypatch.setattr(tools.store, "create", fake_create)

    result = await _call(
        tools.schedule_routine,
        {
            "title": "Flights",
            "instruction": "Check Aeroplan fares YVR to SJD.",
            "cadence": CADENCE,
        },
    )

    assert seen["member_sub"] == "sub-noah"
    assert seen["timezone"] == "America/Vancouver"
    assert seen["cadence"] == CADENCE
    assert "every day at 08:00" in result.content


async def test_an_ambient_turn_cannot_schedule_a_routine(monkeypatch):
    """The fork-bomb guard. A routine firing is an ambient turn, so a routine
    cannot create a routine."""
    from eve.routines import tools

    async def unreachable(**kwargs):
        raise AssertionError("an ambient turn must not author a routine")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.schedule_routine,
        {"title": "X", "instruction": "Y", "cadence": CADENCE},
        state=AMBIENT,
    )

    assert "cannot" in result.content.lower()


async def test_an_ambient_turn_cannot_cancel_a_routine(monkeypatch):
    """A routine that reasons its way to "I should stop" cannot act on it."""
    from eve.routines import tools

    async def unreachable(*args, **kwargs):
        raise AssertionError("an ambient turn must not cancel a routine")

    monkeypatch.setattr(tools.store, "find_by_title", unreachable)
    monkeypatch.setattr(tools.store, "delete", unreachable)

    result = await _call(tools.cancel_routine, {"reference": "Flights"}, state=AMBIENT)

    assert "cannot" in result.content.lower()


async def test_an_ambient_turn_cannot_list_routines(monkeypatch):
    from eve.routines import tools

    async def unreachable(*args, **kwargs):
        raise AssertionError("an ambient turn must not enumerate routines")

    monkeypatch.setattr(tools.store, "list_for", unreachable)

    result = await _call(tools.list_routines, {}, state=AMBIENT)

    assert "cannot" in result.content.lower()


async def test_an_invalid_cadence_is_refused_with_a_diagnostic(monkeypatch):
    from eve.routines import tools

    async def unreachable(**kwargs):
        raise AssertionError("must not store an invalid cadence")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.schedule_routine,
        {"title": "X", "instruction": "Y", "cadence": {"every_hours": 0}},
    )

    assert "hourly" in result.content.lower()


async def test_a_member_without_the_grant_is_refused(monkeypatch):
    from eve.routines import tools

    async def unreachable(**kwargs):
        raise AssertionError("must not store without the grant")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.schedule_routine,
        {"title": "X", "instruction": "Y", "cadence": CADENCE},
        config=NO_PERMS,
    )

    assert "permission denied" in result.content.lower()


async def test_an_over_long_instruction_is_refused(monkeypatch):
    from eve.routines import tools

    async def unreachable(**kwargs):
        raise AssertionError("must not store an over-long instruction")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.schedule_routine,
        {"title": "X", "instruction": "y" * 5000, "cadence": CADENCE},
    )

    assert "too long" in result.content.lower()


async def test_a_malformed_expiry_is_refused(monkeypatch):
    from eve.routines import tools

    async def unreachable(**kwargs):
        raise AssertionError("must not store a malformed expiry")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.schedule_routine,
        {
            "title": "X",
            "instruction": "Y",
            "cadence": CADENCE,
            "expires_at": "end of February",
        },
    )

    assert "iso-8601" in result.content.lower()


async def test_listing_reports_cadence_and_last_outcome(monkeypatch):
    from eve.routines import tools

    async def fake_list_for(member_sub):
        return [{**ROW, "last_outcome": "silent", "last_run_at": datetime(2026, 1, 10, tzinfo=UTC)}]

    monkeypatch.setattr(tools.store, "list_for", fake_list_for)

    result = await _call(tools.list_routines, {})

    assert "Flights" in result.content
    assert "every day at 08:00" in result.content


async def test_listing_says_so_when_there_are_none(monkeypatch):
    from eve.routines import tools

    async def fake_list_for(member_sub):
        return []

    monkeypatch.setattr(tools.store, "list_for", fake_list_for)

    result = await _call(tools.list_routines, {})

    assert "no routines" in result.content.lower()


async def test_cancelling_by_title_deletes_the_match(monkeypatch):
    from eve.routines import tools

    deleted = {}

    async def fake_find(member_sub, text):
        return [ROW]

    async def fake_delete(member_sub, routine_id):
        deleted["id"] = routine_id
        return True

    monkeypatch.setattr(tools.store, "find_by_title", fake_find)
    monkeypatch.setattr(tools.store, "delete", fake_delete)

    result = await _call(tools.cancel_routine, {"reference": "flights"})

    assert deleted["id"] == "r-1"
    assert "Flights" in result.content


async def test_an_ambiguous_title_cancels_nothing_and_lists_the_candidates(monkeypatch):
    from eve.routines import tools

    async def fake_find(member_sub, text):
        return [ROW, {**ROW, "id": "r-2", "title": "Flight club"}]

    async def unreachable(*args, **kwargs):
        raise AssertionError("an ambiguous reference must delete nothing")

    monkeypatch.setattr(tools.store, "find_by_title", fake_find)
    monkeypatch.setattr(tools.store, "delete", unreachable)

    result = await _call(tools.cancel_routine, {"reference": "flight"})

    assert "Flights" in result.content
    assert "Flight club" in result.content


async def test_a_storage_failure_degrades_to_a_string(monkeypatch):
    """The global constraint: every tool returns, never raises."""
    from eve.routines import tools

    async def boom(**kwargs):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(tools.store, "create", boom)

    result = await _call(
        tools.schedule_routine,
        {"title": "X", "instruction": "Y", "cadence": CADENCE},
    )

    assert result.content.startswith("error:")
