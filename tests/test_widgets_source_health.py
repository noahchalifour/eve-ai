from __future__ import annotations

import json


async def test_health_latest_is_the_newest_entry(monkeypatch):
    from eve.widgets.sources import base, health

    async def fake_invoke(tool, arguments):
        return json.dumps({"sleep": [{"date": "2026-09-28"}, {"date": "2026-09-27"}]})

    monkeypatch.setattr(base, "invoke", fake_invoke)
    out = await health._read(base.ReadContext("sub-noah", {}), health.HealthParams(metric="sleep"))
    assert out["latest"] == {"date": "2026-09-28"}
    assert len(out["items"]) == 2


def test_health_requires_the_health_permission():
    from eve.widgets.sources import REGISTRY
    from eve.widgets.sources.health import HealthParams

    assert REGISTRY["health"].permissions(HealthParams(metric="sleep")) == {"health"}
