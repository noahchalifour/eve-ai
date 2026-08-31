"""Unit tests for the suggestion node.

One test per outcome, so a regression names its own cause. Every failure
path in this module returns `[]`, which means a test asserting only "empty
list" cannot tell a working skip from a broken model call. Tests that care
about the difference assert on whether the model was called at all
(`model.calls`).

The `eve.suggest.outcome` span attribute is deliberately NOT asserted: this
repo has no span-testing harness, and standing an OpenTelemetry in-memory
exporter up for one attribute would invent an idiom nothing else here uses.
It is an observability signal read in Langfuse, not a behavioural contract.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, HumanMessage

from eve import suggest as suggest_mod
from eve.memory.types import Memory, MemoryBundle


def test_clean_keeps_good_suggestions_in_order():
    assert suggest_mod.clean(["Yes, do it", "What about tomorrow?"]) == [
        "Yes, do it",
        "What about tomorrow?",
    ]


def test_clean_trims_whitespace():
    assert suggest_mod.clean(["  Yes, do it \n"]) == ["Yes, do it"]


def test_clean_drops_empty_and_whitespace_only_entries():
    assert suggest_mod.clean(["", "   ", "Yes"]) == ["Yes"]


def test_clean_drops_overlong_entries():
    """A chip is rendered verbatim in a pill. A paragraph breaks the UI, and
    truncating it mid-word would put words in the member's mouth."""
    assert suggest_mod.clean(["x" * (suggest_mod.MAX_CHARS + 1)]) == []
    assert suggest_mod.clean(["x" * suggest_mod.MAX_CHARS]) == ["x" * suggest_mod.MAX_CHARS]


def test_clean_caps_the_count():
    assert suggest_mod.clean([f"chip {i}" for i in range(9)]) == [
        "chip 0", "chip 1", "chip 2", "chip 3",
    ]


def test_clean_keeps_a_single_suggestion():
    """The prompt asks for 2-4. There is deliberately no floor: discarding a
    usable suggestion, or retrying inside a budget that exists to bound the
    turn, are both worse than one chip."""
    assert suggest_mod.clean(["Yes"]) == ["Yes"]


def test_clean_survives_a_model_returning_the_wrong_type():
    """`with_structured_output` is contracted to return a `Suggestions`, but a
    provider or langchain change that returns a bare dict or a string must
    produce no chips rather than an AttributeError inside the graph."""
    assert suggest_mod.clean(None) == []
    assert suggest_mod.clean("Yes, do it") == []
    assert suggest_mod.clean(["Yes", 7, None]) == ["Yes"]


def test_the_prompt_file_loads_and_names_the_member_voice():
    prompt = suggest_mod.load_suggest_prompt()
    assert "first person" in prompt.lower()


def test_the_settings_defaults_are_on_and_bounded():
    from eve.settings import get_settings

    settings = get_settings()
    assert settings.suggest_enabled is True
    assert settings.suggest_budget_ms == 1500


MEMBER = {
    "sub": "sub-noah",
    "name": "Noah",
    "role": "adult",
    "timezone": "America/Toronto",
    "permissions": [],
    "local_time": "2026-08-31 09:00 EDT",
}


def _state(human="turn the lights off", ai="Which ones?", memory=None):
    return {
        "messages": [HumanMessage(human), AIMessage(ai)],
        "member": MEMBER,
        "system_prompt": "",
        "memory": memory,
        "dynamic_tools": [],
        "suggestions": ["stale chip from the previous turn"],
    }


class FakeModel:
    """Mirrors the surface `suggest` uses: with_structured_output, with_config,
    ainvoke. Records what it was handed so tests can assert on the prompt and
    on TAG_NOSTREAM without a real model."""

    def __init__(self, result=None, error=None, delay=0.0):
        self._result = result
        self._error = error
        self._delay = delay
        self.prompt = None
        self.tags = None
        self.schema = None
        self.calls = 0

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    def with_config(self, **kwargs):
        self.tags = kwargs.get("tags")
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        self.prompt = messages[0].content
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error:
            raise self._error
        return self._result


def _install(monkeypatch, model):
    monkeypatch.setattr(suggest_mod, "get_model", lambda _tier: model)
    return model


async def test_a_good_response_becomes_chips(monkeypatch):
    model = _install(monkeypatch, FakeModel(
        result=suggest_mod.Suggestions(suggestions=["Just the kitchen", "All of them"])
    ))
    result = await suggest_mod.suggest(_state(), {})
    assert result == {"suggestions": ["Just the kitchen", "All of them"]}
    assert model.calls == 1


async def test_the_call_is_reflex_tier_and_never_streams(monkeypatch):
    """Without TAG_NOSTREAM this model's tokens go out on the `messages`
    channel and every client renders them as Eve's reply."""
    from langgraph.constants import TAG_NOSTREAM

    seen = {}
    model = FakeModel(result=suggest_mod.Suggestions(suggestions=["Yes"]))

    def factory(tier):
        seen["tier"] = tier
        return model

    monkeypatch.setattr(suggest_mod, "get_model", factory)
    await suggest_mod.suggest(_state(), {})

    from eve.models import Tier

    assert seen["tier"] is Tier.REFLEX
    assert model.tags == [TAG_NOSTREAM]
    assert model.schema is suggest_mod.Suggestions


async def test_the_prompt_carries_the_exchange_and_the_member_name(monkeypatch):
    model = _install(monkeypatch, FakeModel(
        result=suggest_mod.Suggestions(suggestions=["Yes"])
    ))
    await suggest_mod.suggest(_state(human="lights off", ai="Which ones?"), {})

    assert "lights off" in model.prompt
    assert "Which ones?" in model.prompt
    assert "Noah" in model.prompt


async def test_a_malformed_response_yields_no_chips(monkeypatch):
    """The call succeeded and returned something unusable. Deterministic dead
    end - no retry, no raise."""
    _install(monkeypatch, FakeModel(error=OutputParserException("unknown tool")))
    assert await suggest_mod.suggest(_state(), {}) == {"suggestions": []}


async def test_a_wrong_shaped_response_yields_no_chips(monkeypatch):
    _install(monkeypatch, FakeModel(result={"suggestions": ["Yes"]}))
    assert await suggest_mod.suggest(_state(), {}) == {"suggestions": []}


async def test_a_transient_failure_yields_no_chips(monkeypatch):
    _install(monkeypatch, FakeModel(error=RuntimeError("gemini is down")))
    assert await suggest_mod.suggest(_state(), {}) == {"suggestions": []}


async def test_exceeding_the_budget_yields_no_chips(monkeypatch):
    """The whole point of the budget: a slow REFLEX call must not hold the run
    open. 0ms budget against any awaited call is the deterministic version of
    'too slow'."""
    monkeypatch.setenv("EVE_SUGGEST_BUDGET_MS", "0")
    _install(monkeypatch, FakeModel(
        result=suggest_mod.Suggestions(suggestions=["Yes"]), delay=0.05
    ))
    assert await suggest_mod.suggest(_state(), {}) == {"suggestions": []}


async def test_the_custom_stream_frame_carries_the_same_list(monkeypatch):
    """The Flutter client reads `custom`, not state. Both exits come from one
    helper so they cannot disagree."""
    written = []
    monkeypatch.setattr(suggest_mod, "get_stream_writer", lambda: written.append)
    _install(monkeypatch, FakeModel(
        result=suggest_mod.Suggestions(suggestions=["Just the kitchen"])
    ))

    result = await suggest_mod.suggest(_state(), {})

    assert written == [{"suggestions": ["Just the kitchen"]}]
    assert result["suggestions"] == written[0]["suggestions"]


async def test_an_empty_result_still_emits_a_frame(monkeypatch):
    """An empty list means 'clear the chips', which a client can only act on
    if it arrives."""
    written = []
    monkeypatch.setattr(suggest_mod, "get_stream_writer", lambda: written.append)
    _install(monkeypatch, FakeModel(error=RuntimeError("down")))

    assert await suggest_mod.suggest(_state(), {}) == {"suggestions": []}
    assert written == [{"suggestions": []}]


async def test_the_node_is_safe_without_a_custom_stream_consumer(monkeypatch):
    """A direct call outside a runnable context - exactly what this test
    does - makes `get_stream_writer()` raise `RuntimeError` (it calls
    `get_config()`, which raises outside a runnable context). `_emit` catches
    that and still returns the state channel value, so the node needs no
    branch of its own. Inside a real graph node the writer instead defaults
    to `_no_op_stream_writer` (langgraph/runtime.py:206) when there is no
    `custom` stream consumer."""
    _install(monkeypatch, FakeModel(
        result=suggest_mod.Suggestions(suggestions=["Yes"])
    ))
    assert await suggest_mod.suggest(_state(), {}) == {"suggestions": ["Yes"]}


async def test_an_ambient_turn_gets_no_chips(monkeypatch):
    """An ambient turn is not a member speaking, and its reply goes to a push
    notification, not a chat surface. Asserting no call was made is the point:
    this also saves a REFLEX call per household signal."""
    from eve.state import ambient_marker

    model = _install(monkeypatch, FakeModel(
        result=suggest_mod.Suggestions(suggestions=["Yes"])
    ))
    state = _state(human=f"{ambient_marker('Noah')} the garage door opened")

    assert await suggest_mod.suggest(state, {}) == {"suggestions": []}
    assert model.calls == 0


async def test_the_loop_exhausted_reply_gets_no_chips(monkeypatch):
    """The tools loop gave up. That is not a conversation to offer
    continuations of."""
    from eve.state import LOOP_EXHAUSTED

    model = _install(monkeypatch, FakeModel(
        result=suggest_mod.Suggestions(suggestions=["Yes"])
    ))

    assert await suggest_mod.suggest(_state(ai=LOOP_EXHAUSTED), {}) == {"suggestions": []}
    assert model.calls == 0


async def test_the_kill_switch_makes_no_call(monkeypatch):
    monkeypatch.setenv("EVE_SUGGEST_ENABLED", "false")
    model = _install(monkeypatch, FakeModel(
        result=suggest_mod.Suggestions(suggestions=["Yes"])
    ))

    assert await suggest_mod.suggest(_state(), {}) == {"suggestions": []}
    assert model.calls == 0


async def test_a_turn_with_nothing_said_gets_no_chips(monkeypatch):
    """No human message means there is no member utterance to continue -
    the same guard `_run_extraction` applies before extracting."""
    model = _install(monkeypatch, FakeModel(
        result=suggest_mod.Suggestions(suggestions=["Yes"])
    ))
    state = _state()
    state["messages"] = [AIMessage("Hello.")]

    assert await suggest_mod.suggest(state, {}) == {"suggestions": []}
    assert model.calls == 0


async def test_a_skip_still_clears_the_previous_turns_chips(monkeypatch):
    """The failure this prevents: a client rendering chips that were
    plausible continuations of a conversation that has since moved on."""
    written = []
    monkeypatch.setattr(suggest_mod, "get_stream_writer", lambda: written.append)
    monkeypatch.setenv("EVE_SUGGEST_ENABLED", "false")
    _install(monkeypatch, FakeModel(result=suggest_mod.Suggestions(suggestions=["Yes"])))

    result = await suggest_mod.suggest(_state(), {})

    assert result == {"suggestions": []}
    assert written == [{"suggestions": []}]


async def test_a_state_missing_member_or_messages_gets_no_chips(monkeypatch):
    """A run whose input state omits `member`/`messages` must degrade to no
    chips, not raise a KeyError out of the node - and still return a list
    (not `{}`), so the previous turn's chips are cleared rather than left
    standing."""
    _install(monkeypatch, FakeModel(
        result=suggest_mod.Suggestions(suggestions=["Yes"])
    ))
    assert await suggest_mod.suggest({}, {}) == {"suggestions": []}


async def test_a_missing_member_with_a_real_exchange_gets_no_chips(monkeypatch):
    """Messages present, so the ambient/empty-human skip does not fire, but
    `member` missing: `_render` KeyErrors on `member['name']`, which the
    existing try/except around model preparation converts to no chips rather
    than letting the KeyError escape the node."""
    model = _install(monkeypatch, FakeModel(
        result=suggest_mod.Suggestions(suggestions=["Yes"])
    ))
    state = {"messages": [HumanMessage("lights off"), AIMessage("Which ones?")]}

    assert await suggest_mod.suggest(state, {}) == {"suggestions": []}
    assert model.calls == 0


def _memory(layer: str, content: str) -> Memory:
    now = datetime(2026, 8, 31)
    return Memory(
        id=f"mem-{layer}",
        layer=layer,
        scope_kind="member",
        scope_id="sub-noah",
        kind="fact",
        subject=None,
        content=content,
        confidence=1.0,
        salience=1.0,
        created_at=now,
        last_seen_at=now,
    )


async def test_the_prompt_carries_profile_and_rules_but_not_household_or_episodic(monkeypatch):
    """`_render_memory` narrows the injected bundle to `profile` + `rules` -
    deliberate, since the design doc names the rendered bundle as the
    prompt-injection surface. Pin the narrowing so a later widening (e.g.
    adding `household`/`episodic`) is not silent."""
    model = _install(monkeypatch, FakeModel(
        result=suggest_mod.Suggestions(suggestions=["Yes"])
    ))
    memory: MemoryBundle = {
        "profile": [_memory("profile", "PROFILE_MARKER likes tea in the morning")],
        "household": [_memory("household", "HOUSEHOLD_MARKER quiet hours after 9pm")],
        "episodic": [_memory("episodic", "EPISODIC_MARKER asked about the thermostat")],
        "rules": [_memory("rule", "RULES_MARKER always confirm before locking doors")],
        "digest": None,
        "vector_used": False,
        "latency_ms": 0.0,
    }

    await suggest_mod.suggest(_state(memory=memory), {})

    assert "PROFILE_MARKER" in model.prompt
    assert "RULES_MARKER" in model.prompt
    assert "HOUSEHOLD_MARKER" not in model.prompt
    assert "EPISODIC_MARKER" not in model.prompt


async def test_a_model_factory_failure_yields_no_chips(monkeypatch):
    """`get_model` raising - a realistic startup failure, e.g. a missing
    LiteLLM key - must degrade to no chips like every other failure path
    here."""
    def factory(_tier):
        raise RuntimeError("no LiteLLM key configured")

    monkeypatch.setattr(suggest_mod, "get_model", factory)
    assert await suggest_mod.suggest(_state(), {}) == {"suggestions": []}
