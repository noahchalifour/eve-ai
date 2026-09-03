"""tests/test_specialists_stylist.py"""
import importlib
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage

import eve.specialists.stylist as stylist_module
from tests.conftest import FakeToolCallingModel
from tests.test_specialists_base import MEMBER, STATE

CONFIG = {
    "configurable": {
        "member": {
            "sub": "sub-noah",
            "permissions": ["wardrobe", "calendar.read"],
        }
    }
}


async def test_read_wardrobe_returns_the_rendered_catalogue(monkeypatch):
    monkeypatch.setattr(
        stylist_module.catalog,
        "render_wardrobe",
        AsyncMock(return_value="## top\n- white oxford shirt"),
    )

    result = await stylist_module.read_wardrobe.ainvoke({}, config=CONFIG)

    assert "white oxford shirt" in result
    stylist_module.catalog.render_wardrobe.assert_awaited_once_with("sub-noah")


async def test_todays_weather_relays_home_assistant(monkeypatch):
    monkeypatch.setattr(
        stylist_module, "invoke", AsyncMock(return_value='{"temperature": 8}')
    )

    result = await stylist_module.todays_weather.ainvoke({}, config=CONFIG)

    assert "8" in result
    stylist_module.invoke.assert_awaited_once_with("home.weather", {})


async def test_list_events_requires_calendar_read(monkeypatch):
    invoke = AsyncMock()
    monkeypatch.setattr(stylist_module, "invoke", invoke)
    config = {"configurable": {"member": {"sub": "sub-noah", "permissions": ["wardrobe"]}}}

    result = await stylist_module.list_events.ainvoke({}, config=config)

    assert "Permission denied" in result
    assert "calendar.read" in result
    invoke.assert_not_awaited()


async def test_list_events_passes_the_member_sub(monkeypatch):
    monkeypatch.setattr(stylist_module, "invoke", AsyncMock(return_value="[]"))

    await stylist_module.list_events.ainvoke({}, config=CONFIG)

    stylist_module.invoke.assert_awaited_once_with(
        "calendar.list_events",
        {"member_sub": "sub-noah", "lookahead_minutes": 960, "horizon_days": 1},
    )


async def test_sync_wardrobe_is_bounded_and_reports_what_is_left(monkeypatch):
    monkeypatch.setattr(
        stylist_module.catalog,
        "sync",
        AsyncMock(
            return_value={
                "catalogued": 5,
                "removed": 0,
                "failed": 0,
                "remaining": 12,
                "error": None,
            }
        ),
    )

    result = await stylist_module.sync_wardrobe.ainvoke({}, config=CONFIG)

    assert "5" in result
    assert "12" in result
    kwargs = stylist_module.catalog.sync.await_args.kwargs
    assert kwargs["limit"] == stylist_module.SYNC_LIMIT


async def test_sync_wardrobe_surfaces_an_error(monkeypatch):
    monkeypatch.setattr(
        stylist_module.catalog,
        "sync",
        AsyncMock(
            return_value={
                "catalogued": 0,
                "removed": 0,
                "failed": 0,
                "remaining": 0,
                "error": "error: eve-tools unavailable",
            }
        ),
    )

    result = await stylist_module.sync_wardrobe.ainvoke({}, config=CONFIG)

    assert result.startswith("error:")


async def test_read_wardrobe_degrades_when_the_store_raises(monkeypatch):
    """The binding contract: a tool returns a string and never raises - a
    database failure must not cost the whole specialist turn."""
    monkeypatch.setattr(
        stylist_module.catalog,
        "render_wardrobe",
        AsyncMock(side_effect=RuntimeError("connection refused")),
    )

    result = await stylist_module.read_wardrobe.ainvoke({}, config=CONFIG)

    assert result.startswith("error:")


async def test_sync_wardrobe_degrades_when_the_store_raises(monkeypatch):
    monkeypatch.setattr(
        stylist_module.catalog,
        "sync",
        AsyncMock(side_effect=RuntimeError("connection refused")),
    )

    result = await stylist_module.sync_wardrobe.ainvoke({}, config=CONFIG)

    assert result.startswith("error:")


async def test_a_member_without_the_wardrobe_permission_is_denied():
    state = {**STATE, "member": {**MEMBER, "permissions": ["home.control"]}}

    result = await stylist_module.ask_stylist.ainvoke(
        {"request": "what should I wear", "state": state},
        config={"configurable": {}},
    )

    assert "Permission denied" in result
    assert "wardrobe" in result


async def test_the_stylist_reads_the_wardrobe_through_its_loop(monkeypatch):
    tool_call = {
        "name": "read_wardrobe",
        "args": {},
        "id": "call-1",
        "type": "tool_call",
    }
    monkeypatch.setattr(
        "eve.specialists.stylist._model_for_test",
        lambda: FakeToolCallingModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[tool_call]),
                    AIMessage(content="Wear the navy wool blazer."),
                ]
            )
        ),
    )
    importlib.reload(stylist_module)
    monkeypatch.setattr(
        stylist_module.catalog,
        "render_wardrobe",
        AsyncMock(return_value="## outerwear\n- navy wool blazer"),
    )

    state = {**STATE, "member": {**MEMBER, "permissions": ["wardrobe"]}}
    result = await stylist_module.ask_stylist.ainvoke(
        {"request": "what should I wear", "state": state},
        config={"configurable": {}},
    )

    assert "navy wool blazer" in result
