"""The registry is the whole extension story: a source is one module that
calls `register`, and nothing else in the server has to learn its name."""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict


def test_every_shipped_source_is_registered():
    from eve.widgets import sources

    assert {"series", "health"} <= set(sources.REGISTRY)


def test_parse_rejects_an_unknown_type():
    from eve.widgets.sources import base

    assert base.parse({"type": "nope"}) == "unknown source type 'nope'"


def test_parse_rejects_an_unknown_param_rather_than_ignoring_it():
    """`url`, `token`, `member_sub` must never ride along silently."""
    from eve.widgets.sources import base

    error = base.parse({"type": "health", "metric": "sleep", "url": "http://x"})
    assert isinstance(error, str) and "url" in error


def test_parse_returns_the_type_and_validated_params():
    from eve.widgets.sources import base

    source, params = base.parse({"type": "health", "metric": "sleep"})
    assert source.name == "health"
    assert params.metric == "sleep"


def test_register_refuses_a_duplicate_name():
    from eve.widgets.sources import base

    class P(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def read(ctx, params):
        return {}

    with pytest.raises(ValueError):
        base.register(base.SourceType(name="health", params=P, fields=frozenset(), read=read))


async def test_call_tool_raises_on_a_degraded_call(monkeypatch):
    from eve.widgets.sources import base

    async def fake_invoke(tool, arguments):
        return "error: eve-tools unavailable (ConnectError)"

    monkeypatch.setattr(base, "invoke", fake_invoke)
    with pytest.raises(base.SourceError):
        await base.call_tool("home.get_state", {"entity_id": "light.kitchen"})
