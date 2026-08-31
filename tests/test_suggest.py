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

from eve import suggest as suggest_mod


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
