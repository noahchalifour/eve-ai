# tests/test_widgets_source_calendar.py
from __future__ import annotations

import json


async def test_calendar_formats_local_times(monkeypatch):
    from eve.settings import get_settings
    from eve.widgets.sources import base, calendar

    monkeypatch.setenv("EVE_HOUSEHOLD_TIMEZONE", "America/Vancouver")
    get_settings.cache_clear()

    async def fake_invoke(tool, arguments):
        assert tool == "calendar.list_events"
        assert arguments == {"member_sub": "sub-noah", "lookahead_minutes": 0, "horizon_days": 7}
        return json.dumps({"events": [
            {"summary": "Dentist", "location": "Main St", "start": "2026-09-28T22:00:00+00:00", "end": None},
            {"summary": "Standup", "location": "", "start": "2026-09-29T16:30:00+00:00", "end": None},
        ]})

    monkeypatch.setattr(base, "invoke", fake_invoke)
    out = await calendar._read(base.ReadContext("sub-noah", {}), calendar.CalendarParams(limit=1))

    assert out["items"] == [{"summary": "Dentist", "when": "Mon 3:00 PM", "location": "Main St"}]
    assert out["next"] == out["items"][0]
    assert out["count"] == "2 upcoming"
    get_settings.cache_clear()


async def test_an_empty_calendar_has_a_friendly_next(monkeypatch):
    from eve.widgets.sources import base, calendar

    async def fake_invoke(tool, arguments):
        return json.dumps({"events": []})

    monkeypatch.setattr(base, "invoke", fake_invoke)
    out = await calendar._read(base.ReadContext("s", {}), calendar.CalendarParams())
    assert out["next"]["summary"] == "Nothing scheduled"
    assert out["items"] == []
