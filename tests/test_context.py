from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from eve.context import build_member_context, build_system_prompt, load_context
from eve.family import Family, Member, UnknownMemberError
from eve.memory.types import Memory, MemoryBundle

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


def _mem(content: str, layer: str) -> Memory:
    now = datetime.now(UTC)
    return Memory(
        id="m1",
        layer=layer,
        scope_kind="member",
        scope_id="sub-noah",
        kind="fact",
        subject=None,
        content=content,
        confidence=0.7,
        salience=0.5,
        created_at=now,
        last_seen_at=now,
    )


def _bundle(**kw) -> MemoryBundle:
    return MemoryBundle(**{
        "profile": [], "household": [], "episodic": [], "digest": None,
        "vector_used": False, "latency_ms": 0.0, **kw,
    })


MEMBER = {
    "sub": "sub-noah", "name": "Noah", "role": "adult",
    "timezone": "America/Vancouver", "permissions": [],
    "local_time": "2026-08-18 09:00 PDT",
}


def test_prompt_without_memory_is_unchanged():
    """Phase 1 callers and any turn where memory is empty must not gain a
    dangling empty heading, which reads to the model as 'you know nothing'."""
    prompt = build_system_prompt("You are Eve.", MEMBER)
    assert "What you remember" not in prompt


def test_empty_bundle_adds_no_heading():
    prompt = build_system_prompt("You are Eve.", MEMBER, _bundle())
    assert "What you remember" not in prompt


def test_each_populated_layer_gets_its_own_section():
    bundle = _bundle(
        profile=[_mem("Noah is vegetarian", "profile")],
        household=[_mem("The dog is Cooper", "household")],
        episodic=[_mem("Replacing the dishwasher in March", "episodic")],
        digest="They were planning dinner.",
    )
    prompt = build_system_prompt("You are Eve.", MEMBER, bundle)
    assert "Noah is vegetarian" in prompt
    assert "The dog is Cooper" in prompt
    assert "Replacing the dishwasher in March" in prompt
    assert "They were planning dinner." in prompt


def test_layers_are_labelled_by_confidence_not_merged():
    """Episodic recall is a guess and standing facts are not. Presenting them
    as one undifferentiated list invites Eve to state a fuzzy match with the
    same certainty as a profile fact."""
    bundle = _bundle(
        profile=[_mem("Noah is vegetarian", "profile")],
        episodic=[_mem("Something from a past conversation", "episodic")],
    )
    prompt = build_system_prompt("You are Eve.", MEMBER, bundle)
    assert prompt.index("Noah is vegetarian") < prompt.index(
        "Something from a past conversation"
    )
    assert "may be relevant" in prompt
