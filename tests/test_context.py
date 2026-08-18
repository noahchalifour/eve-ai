from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from eve.context import build_member_context, build_system_prompt, load_context
from eve.family import Family, Member, UnknownMemberError

NOAH = Member(
    sub="sub-noah",
    name="Noah",
    role="adult",
    timezone="America/Toronto",
    permissions=frozenset({"spend", "home.control"}),
)
FIXED_NOW = datetime(2026, 8, 17, 14, 30, tzinfo=ZoneInfo("America/Toronto"))


def test_member_context_localises_the_clock():
    ctx = build_member_context(NOAH, FIXED_NOW)
    assert ctx["name"] == "Noah"
    assert ctx["role"] == "adult"
    assert sorted(ctx["permissions"]) == ["home.control", "spend"]
    assert "2026-08-17" in ctx["local_time"]
    assert "14:30" in ctx["local_time"]


def test_system_prompt_contains_persona_and_member():
    ctx = build_member_context(NOAH, FIXED_NOW)
    prompt = build_system_prompt("You are Eve.", ctx)
    assert "You are Eve." in prompt
    assert "Noah" in prompt
    assert "2026-08-17" in prompt


class PrincipalObject:
    """Stands in for `aegra_api.models.auth.User`: an attribute-only principal
    with no `__getitem__`. Aegra injects one of these into
    `config["configurable"]["langgraph_auth_user"]` on every real run."""

    def __init__(self, identity: str) -> None:
        self.identity = identity


@pytest.mark.parametrize(
    "principal",
    [
        pytest.param({"identity": "sub-noah"}, id="mapping"),
        pytest.param(PrincipalObject("sub-noah"), id="object"),
    ],
)
async def test_load_context_resolves_the_authenticated_member(monkeypatch, principal):
    """Both principal shapes must resolve. The mapping is what the LangGraph
    SDK documents and what a hand-built test config looks like; the object is
    what Aegra actually injects, and subscripting it raised
    `TypeError: 'User' object is not subscriptable` on every real run."""
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    config = {"configurable": {"langgraph_auth_user": principal}}
    result = await load_context({"messages": []}, config)

    assert result["member"]["name"] == "Noah"
    assert result["member"]["sub"] == "sub-noah"
    assert "You are Eve." in result["system_prompt"]


async def test_load_context_rejects_a_subject_not_in_the_roster(monkeypatch):
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    config = {"configurable": {"langgraph_auth_user": {"identity": "sub-stranger"}}}
    with pytest.raises(UnknownMemberError):
        await load_context({"messages": []}, config)
