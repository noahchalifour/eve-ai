# Reply suggestions ("chips") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After Eve answers, produce 2-4 short first-person utterances the member could send next, and deliver them to clients over the LangGraph `custom` stream channel plus a `suggestions` state channel.

**Architecture:** A new `suggest` node runs between `extract` and `END`, making one REFLEX-tier structured-output call bounded by a timeout that degrades to `[]`. It writes the list to a new `suggestions` state channel *and* emits it as `{"suggestions": [...]}` on the `custom` stream channel, from one helper so the two cannot drift. Placement after `extract` is deliberate: with background extraction (the default) the two REFLEX calls overlap, so the turn pays `max()` not `sum()`.

**Tech Stack:** Python 3.12, LangGraph 1.2.11, langchain-core, langchain-openai, Aegra (aegra-api 0.10.3), pydantic v2, pytest + pytest-asyncio (`asyncio_mode = "auto"` — async tests need no decorator), OpenTelemetry.

**Spec:** [`docs/superpowers/specs/2026-08-31-eve-suggestions-design.md`](../specs/2026-08-31-eve-suggestions-design.md)
- Linear EVE-7 — <https://linear.app/chalifour-development/issue/EVE-7/add-suggestions-generation>
- Client half (out of scope here): Linear OPENA-14 — <https://linear.app/chalifour-development/issue/OPENA-14/render-server-driven-reply-suggestions-from-the-langgraph-custom>

## Global Constraints

Copy these values verbatim. Every task's requirements implicitly include this section.

- **Custom stream payload:** exactly `{"suggestions": [...]}` — a JSON array of strings under the key `suggestions`, at the top level of the custom payload. Never `null`.
- **Caps:** at most **4** suggestions; each at most **80** characters after trimming. No minimum count — one valid chip ships as one chip.
- **Default budget:** `EVE_SUGGEST_BUDGET_MS = 1500`. **Default enabled:** `EVE_SUGGEST_ENABLED = true`.
- **Settings prefix is `EVE_`** (`src/eve/settings.py:16-18`), so the field `suggest_budget_ms` is set by `EVE_SUGGEST_BUDGET_MS`.
- **Tier is `Tier.REFLEX`** (`gemini/gemini-flash-lite-latest`). Never VOICE.
- **Every REFLEX call in this feature carries `tags=[TAG_NOSTREAM]`** (`from langgraph.constants import TAG_NOSTREAM`). Without it the suggestion model's tokens stream to clients on the `messages` channel and get rendered as Eve's reply. This is the single most damaging mistake available in this plan.
- **Never raise.** Every failure path returns `[]`. A member must never lose a reply, and a turn must never hang, because chip generation failed.
- **Every exit returns a list**, including skips — returning `{}` would leave the previous turn's chips in state.
- Tests are the unit tier by default; `pytest` already excludes `integration`, `live`, `docker` markers (`pyproject.toml`).
- Run tests with `uv run pytest`.

---

## File Structure

**Created:**
- `src/eve/suggest.py` — the node, its schema, prompt rendering, validation, budget, delivery, and span. One file: it is one node with one call, and splitting validation from the call would spread ~120 lines across two files for no boundary anyone needs.
- `prompts/suggest.md` — the REFLEX prompt, loaded at runtime like `prompts/ambient_filter.md`.
- `tests/test_suggest.py` — unit tests for the node.
- `docs/adr/0013-suggestions-are-a-separate-reflex-call.md`

**Modified:**
- `src/eve/state.py` — add the `suggestions` channel; rename `_replace_dynamic_tools` to `_last_write_wins` and share it; take ownership of the loop-exhausted sentence.
- `src/eve/graph.py` — wire the node, add the `suggest_fn` seam, import the relocated constant.
- `src/eve/settings.py` — two settings.
- `src/eve/memory/extract.py` — rename `_last_exchange` to `last_exchange` so `suggest` can import it.
- `src/eve/eval/replay.py` — pass a no-op `suggest_fn`; update a stale comment.
- `scripts/chat.py` — render chips.
- `tests/test_graph.py` — update two imports of the relocated constant; add wiring tests.
- `tests/test_state.py` — add channel tests.
- `tests/test_memory_extract.py` — update a docstring naming `_last_exchange`.
- `tests/test_live_models.py` — one live chip test.
- `docs/architecture.md` — a section for the node.

---

## Task 1: Shared state — the `suggestions` channel, one reducer, one sentence

`suggest` needs two things that currently live in `graph.py` or nowhere: a state channel to write to, and the loop-exhausted sentence to recognise. `graph.py` will import `suggest`, so `suggest` cannot import from `graph.py` — the constant has to move to `state.py`, which already owns exactly this kind of cross-module literal (`AMBIENT_MARKER_PREFIX`, with the comment "One owner for the literal, deliberately").

**Files:**
- Modify: `src/eve/state.py:52-63` (the reducer), `src/eve/state.py:78-96` (the TypedDict)
- Modify: `src/eve/graph.py:106` (comment), `src/eve/graph.py:118-121` (constant removal), `src/eve/graph.py:134`
- Modify: `src/eve/eval/replay.py:73` (comment)
- Modify: `tests/test_graph.py:431`, `tests/test_graph.py:474` (imports)
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `eve.state.LOOP_EXHAUSTED: str` — the exact sentence, unchanged in wording.
  - `eve.state._last_write_wins(_old: list, new: list) -> list`
  - `EveState["suggestions"]: Annotated[list[str], _last_write_wins]`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_state.py`:

```python
def test_eve_state_carries_suggestions():
    from eve.state import EveState

    state: EveState = {
        "messages": [],
        "member": {
            "sub": "sub-noah",
            "name": "Noah",
            "role": "adult",
            "timezone": "America/Vancouver",
            "permissions": [],
            "local_time": "2026-08-21 09:00 PDT",
        },
        "system_prompt": "",
        "memory": None,
        "dynamic_tools": [],
        "suggestions": ["Yes, do it"],
    }
    assert state["suggestions"] == ["Yes, do it"]


def test_the_shared_reducer_replaces_rather_than_appends():
    """Chips describe ONE turn. Appending would accumulate the whole
    conversation's suggestions, and a client would render chips that were
    plausible three turns ago."""
    from eve.state import _last_write_wins

    assert _last_write_wins(["old"], ["new"]) == ["new"]
    assert _last_write_wins(["old"], []) == []


def test_the_loop_exhausted_sentence_has_one_owner():
    """`suggest` must recognise this reply to skip chips for it, and
    `graph.py` imports `suggest`, so the literal cannot live in graph.py."""
    from eve.graph import _LOOP_EXHAUSTED
    from eve.state import LOOP_EXHAUSTED

    assert _LOOP_EXHAUSTED is LOOP_EXHAUSTED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_state.py -v -k "suggestions or reducer or loop_exhausted"`
Expected: FAIL — `ImportError: cannot import name '_last_write_wins'` and `cannot import name 'LOOP_EXHAUSTED'`.

- [ ] **Step 3: Rename the reducer and add the channel**

In `src/eve/state.py`, rename `_replace_dynamic_tools` to `_last_write_wins`, generalise its signature and docstring, and add the new channel. Replace the existing function with:

```python
def _last_write_wins(_old: list, new: list) -> list:
    """Last-write-wins, shared by `dynamic_tools` and `suggestions`.

    A reducer is what gives a channel a default: without one LangGraph uses
    `LastValue`, which holds no value at all until something writes it, so on
    a fresh thread the key is simply absent from state and every tool taking
    `Annotated[EveState, InjectedState]` fails pydantic validation of the
    injected state before its body ever runs.

    Not `operator.add`, for both channels and for different reasons.
    `search_skills` already merges against the existing list and caps it, then
    returns the whole new list. `suggestions` describes ONE turn: appending
    would accumulate every turn's chips and a client would render
    continuations of a conversation that has moved on.
    """
    return new
```

Add to the end of `class EveState`:

```python
    # Written by `suggest` (eve/suggest.py) after the answer has streamed;
    # read by any client on `stream_mode="values"`/`"updates"` or from
    # `GET /threads/{id}/state`. The same list also goes out on the `custom`
    # stream channel, which is what the Flutter client actually consumes -
    # see the design doc section 6.
    #
    # ALWAYS written, including as `[]`: a turn that skips chip generation
    # must clear the previous turn's chips rather than leave them standing.
    suggestions: Annotated[list[str], _last_write_wins]
```

Add above `class MemberContext`, next to `ambient_marker`:

```python
# Owned here rather than in graph.py because `suggest` must recognise this
# reply to skip chip generation for it, and graph.py imports `suggest` - the
# same one-owner-for-a-shared-literal reason as AMBIENT_MARKER_PREFIX above.
LOOP_EXHAUSTED = (
    "I wasn't able to finish that - I kept going back and forth with my tools "
    "without getting anywhere. Could you try asking me a different way?"
)
```

Update the `dynamic_tools` annotation to `Annotated[list[DynamicToolSpec], _last_write_wins]`.

- [ ] **Step 4: Point graph.py at the relocated constant**

In `src/eve/graph.py`, delete the `_LOOP_EXHAUSTED = (...)` block at lines 118-121 and add to the existing `from eve.state import EveState` line:

```python
from eve.state import LOOP_EXHAUSTED as _LOOP_EXHAUSTED, EveState
```

The local alias is deliberate: `graph.py:134` and two tests already reference `_LOOP_EXHAUSTED`, and aliasing keeps this task to a constant move rather than a rename touching test assertions.

Update the comment at `src/eve/graph.py:106` — replace `_replace_dynamic_tools` with `_last_write_wins`. Do the same at `src/eve/eval/replay.py:73`, where the comment reads "eve/state.py's _replace_dynamic_tools exists to prevent".

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_state.py tests/test_graph.py -v`
Expected: PASS, all of them. `tests/test_graph.py:431` and `:474` import `_LOOP_EXHAUSTED` from `eve.graph` and still resolve through the alias, so they need no edit.

- [ ] **Step 6: Commit**

```bash
git add src/eve/state.py src/eve/graph.py src/eve/eval/replay.py tests/test_state.py
git commit -m "feat(state): add a suggestions channel and share the last-write-wins reducer"
```

---

## Task 2: Share `last_exchange`

`suggest` needs the last human/AI pair. `memory/extract.py` already computes it as a module-private `_last_exchange`. Two functions reading "the last exchange" that can drift is worse than one shared name.

**Files:**
- Modify: `src/eve/memory/extract.py:150` (definition), `src/eve/memory/extract.py:229` (caller)
- Modify: `tests/test_memory_extract.py:433` (docstring mentioning it)
- Test: `tests/test_memory_extract.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `eve.memory.extract.last_exchange(messages: list) -> tuple[str, str]` — `(human, ai)`, each `str`, each `""` when absent. Unchanged behaviour.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_memory_extract.py`:

```python
def test_last_exchange_is_importable_under_a_public_name():
    """`eve.suggest` imports this. A leading underscore across a module
    boundary is a lie about the name's audience."""
    from langchain_core.messages import AIMessage, HumanMessage

    from eve.memory.extract import last_exchange

    human, ai = last_exchange([HumanMessage("hi"), AIMessage("hello")])
    assert (human, ai) == ("hi", "hello")


def test_last_exchange_returns_empty_strings_when_a_side_is_missing():
    from langchain_core.messages import HumanMessage

    from eve.memory.extract import last_exchange

    assert last_exchange([HumanMessage("hi")]) == ("hi", "")
    assert last_exchange([]) == ("", "")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_memory_extract.py -v -k last_exchange`
Expected: FAIL — `ImportError: cannot import name 'last_exchange'`.

- [ ] **Step 3: Rename it**

In `src/eve/memory/extract.py`, rename the definition at line 150 from `_last_exchange` to `last_exchange` and add a docstring:

```python
def last_exchange(messages: list) -> tuple[str, str]:
    """The last thing the member said and the last thing Eve said, as plain
    strings. Public because `eve.suggest` reads the same pair; one owner so
    the two cannot drift."""
    human = next(
        (m.content for m in reversed(messages) if isinstance(m, HumanMessage)), ""
    )
    ai = next((m.content for m in reversed(messages) if isinstance(m, AIMessage)), "")
    return str(human), str(ai)
```

Update the caller at line 229 to `human, ai = last_exchange(state["messages"])`. Update the docstring at `tests/test_memory_extract.py:433` to say `last_exchange` instead of `_last_exchange`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_memory_extract.py -v`
Expected: PASS. Then confirm nothing else referenced the old name: `rg "_last_exchange" src tests` should print nothing.

- [ ] **Step 5: Commit**

```bash
git add src/eve/memory/extract.py tests/test_memory_extract.py
git commit -m "refactor(memory): make last_exchange public for eve.suggest"
```

---

## Task 3: Settings, prompt, and output validation

Everything in the node that needs no model call. Validating first means the model-call task in Task 4 has nothing to prove about output shape.

**Files:**
- Create: `src/eve/suggest.py`
- Create: `prompts/suggest.md`
- Modify: `src/eve/settings.py` (after the `memory_extract_join_budget_ms` block, around line 90)
- Test: `tests/test_suggest.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `eve.suggest.MAX_SUGGESTIONS: int = 4`, `eve.suggest.MAX_CHARS: int = 80`
  - `eve.suggest.Suggestions` — pydantic model, field `suggestions: list[str]`
  - `eve.suggest.clean(raw: object) -> list[str]`
  - `eve.suggest.load_suggest_prompt() -> str`
  - `eve.settings.Settings.suggest_enabled: bool`, `.suggest_budget_ms: int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_suggest.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_suggest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve.suggest'`.

- [ ] **Step 3: Add the settings**

In `src/eve/settings.py`, immediately after the `memory_extract_join_budget_ms: int = 5000` line:

```python
    # Reply suggestions (EVE-7). See docs/superpowers/specs/
    # 2026-08-31-eve-suggestions-design.md.
    #
    # Default ON, unlike ambient_enabled and sandbox_enabled: this subsystem
    # reaches nothing outside the process and writes nothing durable.
    suggest_enabled: bool = True
    # Bounds how long the run stays open after Eve's last token. Missing the
    # budget ships no chips rather than delaying the turn ending - the same
    # degradation as recall's embedding arm.
    suggest_budget_ms: int = 1500
```

- [ ] **Step 4: Create the prompt**

Create `prompts/suggest.md`:

```markdown
You write the next thing a family member might say to Eve, their household
assistant. You are not Eve. You are drafting the MEMBER's side of the
conversation, in first person, as if they typed it themselves.

Given the last exchange, return 2 to 4 short options for what they might send
next. They will be shown as tappable chips and sent verbatim, so each one must
read as something a person would actually type.

Rules:
- First person, from the member. "Yes, do it" - never "Would you like me to?"
- Short. A few words each. Under 80 characters, and much shorter is better.
- Genuinely different from each other. Do not offer one idea reworded.
- Follow from what Eve just said. If she asked a question, some options should
  answer it. If she listed things, some options should pick one.
- Natural, not eager. No exclamation marks. Never thank Eve.
- If the conversation has plainly finished and there is nothing worth saying
  next, return an empty list. Silence is a valid answer.

Do not suggest anything the member has already said in this exchange, and do
not invent facts about the household that are not in what you were given.
```

- [ ] **Step 5: Write the minimal implementation**

Create `src/eve/suggest.py`:

```python
"""Reply suggestions: 2-4 things the MEMBER might say next.

One REFLEX-tier structured-output call after Eve's answer has streamed. Not
part of Eve's own turn - see ADR 0013 and the design doc section 2 for why
folding chips into the VOICE call was rejected.

Every failure degrades to no chips. A member must never lose a reply, and a
turn must never hang, because chip generation had a bad day.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import BaseModel, Field

from eve.settings import get_settings

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 4
# Rendered verbatim in a pill. Anything longer is a paragraph, and truncating
# mid-word would put words in the member's mouth.
MAX_CHARS = 80


class Suggestions(BaseModel):
    """The REFLEX model's structured output.

    `default_factory` matters: the prompt licenses an empty list for a
    finished conversation, and a required field would make that answer a
    validation failure indistinguishable from a broken response.
    """

    suggestions: list[str] = Field(
        default_factory=list,
        description="2-4 short first-person things the member might say next.",
    )


def clean(raw: object) -> list[str]:
    """Validate hard: chips are rendered verbatim by a client.

    Takes `object`, not `list[str]`, on purpose. `with_structured_output` is
    contracted to return a `Suggestions`, but a provider or langchain change
    that returns a bare dict or a string must produce no chips rather than an
    AttributeError escaping into the graph.
    """
    if not isinstance(raw, list):
        return []
    kept: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or len(text) > MAX_CHARS:
            continue
        kept.append(text)
        if len(kept) == MAX_SUGGESTIONS:
            break
    return kept


@lru_cache(maxsize=1)
def load_suggest_prompt() -> str:
    return (get_settings().prompt_file.parent / "suggest.md").read_text()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_suggest.py -v`
Expected: PASS, all nine.

- [ ] **Step 7: Commit**

```bash
git add src/eve/suggest.py prompts/suggest.md src/eve/settings.py tests/test_suggest.py
git commit -m "feat(suggest): add the suggestion schema, prompt, and output validation"
```

---

## Task 4: The node — the call, the three outcomes, the budget, and delivery

**Files:**
- Modify: `src/eve/suggest.py`
- Test: `tests/test_suggest.py`

**Interfaces:**
- Consumes: `eve.suggest.clean`, `.load_suggest_prompt`, `.Suggestions`, `.MAX_SUGGESTIONS` (Task 3); `eve.memory.extract.last_exchange` (Task 2); `EveState["suggestions"]` (Task 1).
- Produces: `async eve.suggest.suggest(state: EveState, config: RunnableConfig) -> dict` — always returns `{"suggestions": list[str]}`, never raises. This is the callable `build_graph`'s `suggest_fn` defaults to in Task 5.

- [ ] **Step 1: Write the failing tests**

Add these to the imports at the TOP of `tests/test_suggest.py`, beside the
existing `from eve import suggest as suggest_mod`:

```python
import asyncio

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, HumanMessage
```

Then append the rest to `tests/test_suggest.py`:

```python
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
    """`stream_writer` defaults to `_no_op_stream_writer`
    (langgraph/runtime.py:206), so the node needs no branch - but the graph
    calls it under plain `ainvoke` in eval and in tests, so pin it."""
    _install(monkeypatch, FakeModel(
        result=suggest_mod.Suggestions(suggestions=["Yes"])
    ))
    assert await suggest_mod.suggest(_state(), {}) == {"suggestions": ["Yes"]}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_suggest.py -v -k "good_response or reflex or prompt_carries or malformed or wrong_shaped or transient or budget or custom_stream or empty_result or without_a_custom"`
Expected: FAIL — `AttributeError: module 'eve.suggest' has no attribute 'suggest'`.

- [ ] **Step 3: Write the implementation**

Add to `src/eve/suggest.py`. New imports at the top:

```python
import asyncio

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.constants import TAG_NOSTREAM
from opentelemetry import trace
from pydantic import ValidationError

from eve.memory.extract import last_exchange
from eve.memory.types import MemoryBundle
from eve.models import Tier, get_model
from eve.state import EveState

_tracer = trace.get_tracer("eve.suggest")
```

Then, after `load_suggest_prompt`:

```python
def _budget_seconds() -> float:
    return get_settings().suggest_budget_ms / 1000.0


def _render_memory(memory: MemoryBundle | None) -> str:
    """Profile and rules only. Household and episodic are what Eve needs to
    ANSWER; what shapes a plausible member utterance is who they are and how
    they like to be talked to. Keeping this narrow also keeps a REFLEX prompt
    short."""
    if not memory:
        return "(nothing recorded)"
    lines = [f"- {m.content}" for m in (*memory["profile"], *memory["rules"])]
    return "\n".join(lines) if lines else "(nothing recorded)"


def _render(member: dict, human: str, ai: str, memory: MemoryBundle | None) -> str:
    return (
        f"{load_suggest_prompt()}\n\n"
        f"## Who is talking to Eve\n"
        f"{member['name']} ({member['role']}), local time {member['local_time']}\n\n"
        f"## What Eve knows about them\n{_render_memory(memory)}\n\n"
        f"## The exchange\n{member['name']}: {human}\nEve: {ai}\n"
    )


def _emit(chips: list[str], span) -> dict:
    """The ONE exit. Two delivery paths - the `custom` stream frame the
    Flutter client consumes, and the state channel stock SDK clients and
    `GET /threads/{id}/state` read - so they must be written in one place or
    they will eventually disagree.

    `get_stream_writer()` is called unconditionally: it defaults to
    `_no_op_stream_writer` (langgraph/runtime.py:206), so this is inert under
    `ainvoke` with no `custom` stream mode.
    """
    span.set_attribute("eve.suggest.count", len(chips))
    try:
        get_stream_writer()({"suggestions": chips})
    except Exception:
        # Delivering chips must not be able to fail a turn. A writer that
        # raises (no runtime context, a future langgraph change) still leaves
        # the state channel written below.
        logger.warning("could not emit the suggestions frame", exc_info=True)
    return {"suggestions": chips}


async def suggest(state: EveState, config: RunnableConfig) -> dict:
    """Chips for the turn that just finished. Never raises; every failure is
    an empty list."""
    with _tracer.start_as_current_span("eve.suggest") as span:
        human, ai = last_exchange(state["messages"])
        member = state["member"]

        try:
            model = get_model(Tier.REFLEX).with_structured_output(Suggestions)
            prompt = _render(member, human, ai, state.get("memory"))
        except Exception:
            logger.warning("suggestions could not be prepared", exc_info=True)
            span.set_attribute("eve.suggest.outcome", "error")
            return _emit([], span)

        try:
            async with asyncio.timeout(_budget_seconds()):
                result = await model.with_config(tags=[TAG_NOSTREAM]).ainvoke(
                    [HumanMessage(prompt)]
                )
        except TimeoutError:
            # Bounded exposure, not a bug: the run does not stay open for a
            # slow REFLEX call. If this fires often, the budget is too tight
            # or the tier is too slow - `eve.suggest.outcome` is how that
            # becomes visible rather than folklore.
            span.set_attribute("eve.suggest.outcome", "budget")
            return _emit([], span)
        except (ValidationError, ValueError, OutputParserException) as exc:
            # The call succeeded; what came back cannot be used. Same
            # category, and the same three exception types, as
            # eve_ambient/filter.py's malformed branch.
            logger.warning("suggestions came back unusable: %s", exc)
            span.set_attribute("eve.suggest.outcome", "malformed")
            return _emit([], span)
        except Exception:
            logger.warning("suggestions failed", exc_info=True)
            span.set_attribute("eve.suggest.outcome", "error")
            return _emit([], span)

        chips = clean(getattr(result, "suggestions", None))
        span.set_attribute("eve.suggest.outcome", "ok" if chips else "empty")
        return _emit(chips, span)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_suggest.py -v`
Expected: PASS, all of them.

Note on `test_a_wrong_shaped_response_yields_no_chips`: a returned `dict` has no `suggestions` attribute, so `getattr(result, "suggestions", None)` is `None` and `clean` returns `[]`. That is the belt-and-braces path `eve_ambient/filter.py` has as an explicit `isinstance` check; here `clean`'s `object` signature already covers it.

- [ ] **Step 5: Commit**

```bash
git add src/eve/suggest.py tests/test_suggest.py
git commit -m "feat(suggest): add the REFLEX node with a bounded call and two delivery paths"
```

---

## Task 5: The skips

Three turns must not generate chips. Each returns `[]` rather than `{}`, so the previous turn's chips are cleared rather than left standing.

**Files:**
- Modify: `src/eve/suggest.py`
- Test: `tests/test_suggest.py`

**Interfaces:**
- Consumes: `eve.state.is_ambient_text`, `eve.state.LOOP_EXHAUSTED` (Task 1); `suggest` (Task 4).
- Produces: no new names. `suggest` gains three early exits.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_suggest.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_suggest.py -v -k "ambient_turn or loop_exhausted or kill_switch or nothing_said or clears_the_previous"`
Expected: FAIL — the fake model is called, so `model.calls == 0` fails and the ambient/kill-switch cases return chips.

- [ ] **Step 3: Add the early exits**

In `src/eve/suggest.py`, add to the imports:

```python
from eve.state import LOOP_EXHAUSTED, EveState, is_ambient_text
```

Insert into `suggest`, immediately after `member = state["member"]` and before the `try` that builds the model:

```python
        # Ordered cheapest-first, and all before the model is even
        # constructed: each of these saves a REFLEX call, and the ambient one
        # fires on every household signal.
        if not get_settings().suggest_enabled:
            span.set_attribute("eve.suggest.outcome", "disabled")
            return _emit([], span)
        if not human or is_ambient_text(human):
            # No human message: nothing to continue. Ambient-marked: not a
            # member speaking, and the reply goes to ntfy, not a chat
            # surface. Both are "no member utterance here", so one branch.
            span.set_attribute("eve.suggest.outcome", "skipped")
            return _emit([], span)
        if ai == LOOP_EXHAUSTED:
            span.set_attribute("eve.suggest.outcome", "skipped")
            return _emit([], span)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_suggest.py -v`
Expected: PASS, all of them.

- [ ] **Step 5: Commit**

```bash
git add src/eve/suggest.py tests/test_suggest.py
git commit -m "feat(suggest): skip ambient turns, exhausted loops, and the disabled case"
```

---

## Task 6: Wire it into the graph

**Files:**
- Modify: `src/eve/graph.py:130-190` (the `build_graph` signature and the builder block at the end)
- Modify: `src/eve/eval/replay.py:86-90`
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `eve.suggest.suggest` (Tasks 4-5).
- Produces: `build_graph(model_factory=get_model, recall_fn=memory_recall, extract_fn=memory_extract, suggest_fn=suggest_node) -> StateGraph`, with a `suggest` node between `extract` and `END`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_graph.py`:

```python
async def _no_suggest(state, config):
    return {"suggestions": []}


async def test_suggestions_reach_final_state(monkeypatch):
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    async def fake_suggest(state, config):
        return {"suggestions": ["Just the kitchen"]}

    app = build_graph(
        model_factory=_fake_factory,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=fake_suggest,
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert result["suggestions"] == ["Just the kitchen"]


async def test_suggest_runs_after_extract(monkeypatch):
    """Deliberate ordering: with background extraction (the default),
    `extract` returns as soon as it registers its task, so its REFLEX call
    and the suggestion call overlap. Reversing these serialises them for no
    gain (ADR 0012, ADR 0013)."""
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")
    order = []

    async def recording_extract(state, config):
        order.append("extract")
        return {}

    async def recording_suggest(state, config):
        order.append("suggest")
        return {"suggestions": []}

    app = build_graph(
        model_factory=_fake_factory,
        recall_fn=_no_recall,
        extract_fn=recording_extract,
        suggest_fn=recording_suggest,
    ).compile()
    await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert order == ["extract", "suggest"]


async def test_suggestions_default_to_empty_on_a_fresh_thread(monkeypatch):
    """The reducer is what gives the channel a default. Without it the key is
    absent and every tool taking InjectedState fails pydantic validation
    (graph.py's own comment on `_last_write_wins`)."""
    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    async def reads_state(state, config):
        assert state["suggestions"] == []
        return {"suggestions": []}

    app = build_graph(
        model_factory=_fake_factory,
        recall_fn=_no_recall,
        extract_fn=_no_extract,
        suggest_fn=reads_state,
    ).compile()
    result = await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert result["suggestions"] == []


async def test_the_suggestion_node_is_wired_by_default(monkeypatch):
    """The seam exists for tests and eval. The DEFAULT must be the real node,
    or the feature ships wired to nothing."""
    from eve.graph import build_graph as real_build_graph

    graph = real_build_graph()
    assert "suggest" in graph.nodes
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_graph.py -v -k suggest`
Expected: FAIL — `TypeError: build_graph() got an unexpected keyword argument 'suggest_fn'`.

- [ ] **Step 3: Wire the node**

In `src/eve/graph.py`, add the import:

```python
from eve.suggest import suggest as suggest_node
```

Change the signature:

```python
def build_graph(
    model_factory=get_model,
    recall_fn=memory_recall,
    extract_fn=memory_extract,
    suggest_fn=suggest_node,
) -> StateGraph:
```

In the builder block at the end of `build_graph`, add the node and re-point the edge. Replace `builder.add_edge("extract", END)` with:

```python
    builder.add_node("suggest", suggest_fn)
    # AFTER extract, not before. With EVE_MEMORY_EXTRACT_BACKGROUND=true (the
    # default) `extract` returns as soon as it registers its task, so
    # extraction's REFLEX call and the suggestion call overlap and the turn
    # pays max() rather than sum(). Reversing them serialises two calls for
    # no gain. See ADR 0013.
    builder.add_edge("extract", "suggest")
    builder.add_edge("suggest", END)
```

Also add `builder.add_node("suggest", suggest_fn)` next to the other `add_node` calls if you prefer them grouped — either placement compiles; keep it beside the edge for locality with the comment.

- [ ] **Step 4: Keep eval off the chip path**

In `src/eve/eval/replay.py`, add the no-op next to `_no_extract` (which is already defined in that file):

```python
async def _no_suggest(state, config):
    """Eval replays score Eve's answer, not her chips. Same reason
    `_no_extract` exists: a replay must not pay for work nothing scores."""
    return {"suggestions": []}
```

and pass it through:

```python
        app = build_graph(
            model_factory=_model_factory,
            extract_fn=_no_extract,
            suggest_fn=_no_suggest,
        ).compile()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_graph.py tests/test_suggest.py tests/test_eval_replay.py -v`
Expected: PASS.

- [ ] **Step 6: Run the whole unit tier**

Run: `uv run pytest`
Expected: PASS. This is the first point where the real node is in the default graph, so any test that invokes `build_graph()` without overrides now reaches it — if something fails here, it is a test that needs `suggest_fn=_no_suggest`, not a bug in the node.

- [ ] **Step 7: Commit**

```bash
git add src/eve/graph.py src/eve/eval/replay.py tests/test_graph.py
git commit -m "feat(graph): run suggest after extract and before END"
```

---

## Task 7: Show chips in the REPL

`scripts/chat.py` is the only client in this repo. Rendering chips there proves the `custom` frame actually arrives through Aegra, which no unit test covers.

**Files:**
- Modify: `scripts/chat.py:56-76`
- Test: manual — this is a REPL against a live server.

**Interfaces:**
- Consumes: the `{"suggestions": [...]}` custom frame (Task 4).
- Produces: nothing importable.

- [ ] **Step 1: Request the custom channel and render the frame**

In `scripts/chat.py`, change the stream call and the loop:

```python
        print("eve> ", end="", flush=True)
        chips: list[str] = []
        async for chunk in client.runs.stream(
            thread_id,
            _ASSISTANT,
            input={"messages": [{"role": "user", "content": text}]},
            stream_mode=["messages-tuple", "custom"],
        ):
            if chunk.event == "custom":
                # The same frame the Flutter client reads (OPENA-14). Printed
                # after the reply rather than inline: it arrives once the
                # answer has finished streaming.
                suggestions = (chunk.data or {}).get("suggestions")
                if isinstance(suggestions, list):
                    chips = [s for s in suggestions if isinstance(s, str)]
                continue
            if chunk.event != "messages":
                continue
            message, _metadata = chunk.data
            if message.get("type") == "AIMessageChunk":
                print(message.get("content", ""), end="", flush=True)
        print()
        if chips:
            print("  " + "   ".join(f"[{chip}]" for chip in chips))
        print()
```

- [ ] **Step 2: Verify against a real server**

In one shell: `uv run aegra dev`

In another: `uv run python scripts/chat.py`, then send `turn off the lights`.

Expected: Eve's reply streams token by token as before, then a row of `[chip]` entries appears beneath it. If the reply streams but no chips appear, check in this order: (1) does the server log an `eve.suggest` warning; (2) is `EVE_SUGGEST_ENABLED` unset or true; (3) does `chunk.event` arrive as something other than `custom` — print `chunk.event` for one turn to find out.

If `stream_mode` as a list is rejected by this Aegra version, fall back to `stream_mode="messages-tuple"` for the reply plus one `await client.threads.get_state(thread_id)` read after the loop, printing `state["values"].get("suggestions")`. That exercises the state channel instead of the frame — a strictly weaker check, so prefer the list form and only fall back if it genuinely fails.

- [ ] **Step 3: Commit**

```bash
git add scripts/chat.py
git commit -m "feat(chat): render reply suggestions in the REPL"
```

---

## Task 8: A live test

Chip quality is exactly what a fake model cannot fail on. This repo verifies model behaviour live rather than assuming it — see `tests/test_live_models.py`'s existing tests and ADR 0004's history.

**Files:**
- Modify: `tests/test_live_models.py` (append)
- Test: itself.

**Interfaces:**
- Consumes: `eve.suggest.suggest`, `.MAX_SUGGESTIONS`, `.MAX_CHARS` (Tasks 3-5).
- Produces: nothing.

- [ ] **Step 1: Write the test**

Append to `tests/test_live_models.py`. The module already carries `pytestmark = [pytest.mark.live, pytest.mark.skipif(...)]`, so no per-test marker is needed:

```python
async def test_the_reflex_tier_produces_usable_reply_suggestions():
    """The one thing a fake cannot check: that a real REFLEX model, given a
    real exchange and this prompt, returns short first-person options rather
    than Eve's side of the conversation."""
    from langchain_core.messages import AIMessage, HumanMessage

    from eve.suggest import MAX_CHARS, MAX_SUGGESTIONS, suggest

    state = {
        "messages": [
            HumanMessage("can you turn off the lights"),
            AIMessage("Which ones - the kitchen, or the whole downstairs?"),
        ],
        "member": {
            "sub": "sub-noah",
            "name": "Noah",
            "role": "adult",
            "timezone": "America/Toronto",
            "permissions": [],
            "local_time": "2026-08-31 21:30 EDT",
        },
        "system_prompt": "",
        "memory": None,
        "dynamic_tools": [],
        "suggestions": [],
    }

    result = await suggest(state, {})
    chips = result["suggestions"]

    assert 1 <= len(chips) <= MAX_SUGGESTIONS, f"got {chips!r}"
    assert all(chip.strip() and len(chip) <= MAX_CHARS for chip in chips)
    # Answering "which ones?" is the obvious continuation. A model that
    # instead returns Eve's next line ("I'll turn them off") fails this.
    assert any(
        word in " ".join(chips).lower()
        for word in ("kitchen", "downstairs", "both", "all")
    ), f"suggestions do not answer the question asked: {chips!r}"
```

- [ ] **Step 2: Run it**

Run: `EVE_LIVE_TESTS=1 uv run pytest tests/test_live_models.py -m live -v -k suggestions`
Expected: PASS. It spends real Google quota on one `gemini-flash-lite-latest` call.

If the final assertion fails, the prompt is wrong, not the test: read what came back and tighten `prompts/suggest.md`'s "Follow from what Eve just said" rule. Do not weaken the assertion — it is the only check in the suite that the prompt produces the member's voice rather than Eve's.

- [ ] **Step 3: Confirm the default tier is untouched**

Run: `uv run pytest`
Expected: PASS, and the live test is not collected (`addopts` excludes the `live` marker).

- [ ] **Step 4: Commit**

```bash
git add tests/test_live_models.py
git commit -m "test(suggest): verify chip quality against the real REFLEX model"
```

---

## Task 9: Documentation

**Files:**
- Create: `docs/adr/0013-suggestions-are-a-separate-reflex-call.md`
- Modify: `docs/architecture.md`
- Test: none — prose.

**Interfaces:**
- Consumes: everything above.
- Produces: nothing importable.

- [ ] **Step 1: Write ADR 0013**

Create `docs/adr/0013-suggestions-are-a-separate-reflex-call.md`, following the Context / Decision / Consequences shape of `docs/adr/0012-extraction-is-detached-and-joined.md`:

```markdown
# 13. Reply suggestions are a separate REFLEX call

**Status:** Accepted
**Date:** 2026-08-31

## Context

Eve should offer 2-4 short things the member could say next, rendered as
tappable chips. The cheap-looking option is to fold them into Eve's own VOICE
call as structured output: one call, no added latency.

One objection commonly raised against that is WRONG, and should not be
recycled: structured output does not preclude binding real tools.
`with_structured_output(schema, tools=[...], strict=True, include_raw=True)`
is supported by langchain-openai >= 0.3.12 and returns
`{"raw", "parsed", "parsing_error"}`, with tool calls arriving on `raw`.
Specialists and dynamic skills would still work.

The reasons that do hold:

- With `method="json_schema"` the model's TEXT output is the JSON, so what
  reaches the `messages` channel - and Aegra's SSE - is `{"reply":"Hi No`
  fragments. Every client would have to partial-parse JSON to render prose.
  That is a permanent tax on every client to save one cheap call.
- History has to stay prose, because `memory/extract.py` and
  `eval/replay.py` read `AIMessage.content` as prose. Storing the parsed
  reply fixes history but makes the streamed and stored forms of the same
  message differ.
- VOICE falls back through LiteLLM to `anthropic/claude-sonnet-5`, so this
  would depend on LiteLLM translating a Responses-API `text.format`
  json_schema onto Anthropic's Messages API - the assumption class that
  killed ADR 0004's first fallback plan.
- `eve` is a cycle, so the node would branch on tool-calls / valid-parse /
  parse-error on every turn forever.
- Chips would be billed at VOICE, with the schema re-sent on every
  intermediate tool round.

Backgrounding the work instead, as ADR 0012 did for extraction, does not
deliver: by the time a detached task finishes, the graph has reached `END` and
the stream is closed. Writing thread state afterwards needs the checkpointer
`eve` deliberately does not own; stashing it in Postgres needs an endpoint
Aegra owns. Every variant ends with the client polling.

## Decision

A `suggest` node runs between `extract` and `END`, making one REFLEX-tier
structured-output call bounded by `EVE_SUGGEST_BUDGET_MS` (default 1500),
degrading to no chips.

It sits AFTER `extract` so that with background extraction the two REFLEX
calls overlap: the turn pays `max()`, not `sum()`.

The list goes out twice from one helper: a `{"suggestions": [...]}` frame on
the `custom` stream channel, and the `suggestions` state channel for
`GET /threads/{id}/state` and stock-SDK clients.

## Consequences

The run stays open for one REFLEX call after the last token. This is the
latency ADR 0012 moved extraction out of the graph to avoid, and the
difference is that ADR 0012's complaint was the turn not looking finished
while doing work the client did not need. Chips are work the client does need;
they cannot render before they exist under any design.

Every failure is an empty list, which makes total failure invisible by
construction. `eve.suggest.outcome` in Langfuse is the named signal -
`ok` / `empty` / `budget` / `malformed` / `error` / `skipped` / `disabled` -
and the only way to notice chips have silently stopped.

Every REFLEX call here carries `TAG_NOSTREAM`. Without it the suggestion
model's tokens go out on the `messages` channel and clients render them as
Eve's reply.

**The feature is invisible to the Flutter client until that client changes**
(Linear OPENA-14). It requests only `messages` and `custom` stream modes,
reads only `values.messages` on restore, and its `custom` handler accepts only
the `assistant_ui` key. The `custom` frame this node emits is dropped on the
floor until then.

Chips are NOT an `assistant-ui/1.0` surface. `actionId` there is allowlisted
to exactly `weather.rangeChanged`, and a tapped surface button sends an
`<assistant-ui-action>` envelope as the user text rather than a plain member
utterance - which is the whole point of a chip.
```

- [ ] **Step 2: Update the graph diagram and node list**

`docs/architecture.md:19` currently reads:

```
START -> load_context -> recall -> eve <-> tools -> extract -> END
```

Change it to:

```
START -> load_context -> recall -> eve <-> tools -> extract -> suggest -> END
```

Change "Five nodes, wired in `src/eve/graph.py`:" at line 23 to "Six nodes,
wired in `src/eve/graph.py`:", and add this bullet after the `extract` bullet:

```markdown
- **`suggest`** (`src/eve/suggest.py`) makes one `REFLEX`-tier
  structured-output call and produces 2-4 short first-person utterances the
  member might send next. It runs AFTER `extract` deliberately: with
  background extraction (the default) `extract` returns as soon as it
  registers its task, so the two REFLEX calls overlap and the turn pays
  `max()` rather than `sum()`. Every failure - timeout, malformed response,
  transient error - yields an empty list rather than raising, so a member
  never loses a reply to it. Ambient-driven turns, the loop-exhausted reply,
  and a turn with no human message are skipped before the model is
  constructed. See ADR 0013.
```

- [ ] **Step 3: Add the subsystem section**

Add this section to `docs/architecture.md` immediately before `## Eval harness`
(line 739), so it sits after `## Ambient` and keeps the file's rough
phase order:

```markdown
## Reply suggestions

`src/eve/suggest.py`, one node, one `REFLEX` call, no storage of its own.

**What it produces.** 2-4 candidate next utterances *by the member*, first
person, short enough to render in a pill: "Yes, do it", "What about
tomorrow?", "Only the kitchen ones". A chip is text the member might have
typed, so tapping one produces an ordinary `HumanMessage` and there is no
inbound protocol to learn. The wire type is `list[str]` - no ids, no types,
no actions.

**Validation.** At most 4 entries, each at most 80 characters after trimming,
empties dropped. There is no minimum: a response validating down to one good
chip ships that one chip. Validation takes `object`, not `list[str]`, so a
provider or langchain change that returns a bare dict produces no chips
rather than an `AttributeError` inside the graph.

**Delivery, two exits from one helper.** A `{"suggestions": [...]}` frame on
LangGraph's `custom` stream channel, and the `suggestions` channel of
`EveState`. The frame is what the Flutter client consumes; the state channel
serves `GET /threads/{id}/state`, `stream_mode="values"`/`"updates"`, and
survives a reload. Both are written in one place so they cannot drift.

An empty list is always emitted rather than omitted. A turn that skips chip
generation must CLEAR the previous turn's chips - otherwise a client renders
continuations of a conversation that has moved on. This is also why the
`suggestions` channel has a reducer: `_last_write_wins` in `src/eve/state.py`,
shared with `dynamic_tools`, replaces rather than appends, and a reducer is
what gives the channel its `[]` default at all.

**`TAG_NOSTREAM` is mandatory** on the call, as it is on every REFLEX call in
`eve/memory/extract.py`. Without it the suggestion model's tokens go out on
the `messages` channel and every client renders them as Eve's reply.

**Settings.**

| Setting | Default | Effect |
|---|---|---|
| `EVE_SUGGEST_ENABLED` | `true` | Off skips the call entirely and clears chips. Default-on, unlike `EVE_AMBIENT_ENABLED` and `EVE_SANDBOX_ENABLED`, because this subsystem reaches nothing outside the process and writes nothing durable. |
| `EVE_SUGGEST_BUDGET_MS` | `1500` | Ceiling on how long the run stays open after Eve's last token. Exceeded means no chips, not a delayed turn. |

**Skips.** No chips for an ambient-driven turn (not a member speaking, and the
reply goes to ntfy rather than a chat surface - this also saves a REFLEX call
per household signal), for the loop-exhausted reply, or for a turn with no
human message. All three are checked before the model is constructed.

**Observability.** `eve.suggest.outcome` is the one number to look at:
`ok` / `empty` / `budget` / `malformed` / `error` / `skipped` / `disabled`,
plus `eve.suggest.count`. Because every failure degrades to an empty list,
total failure is invisible without this attribute - chips simply stop
appearing and nothing raises. A rising `budget` fraction means the budget is
too tight or the tier too slow.

**Eval.** `eve/eval/replay.py` injects a no-op through `build_graph`'s
`suggest_fn` seam, so replays neither pay for chips nor score them.

**The Flutter client cannot see this yet.** It requests only `messages` and
`custom` stream modes, reads only `values.messages` when restoring a thread,
and its `custom` handler accepts only the `assistant_ui` key - so the frame
this node emits is dropped on the floor. The client change is tracked as
Linear OPENA-14. Chips are deliberately NOT modelled as an `assistant-ui/1.0`
surface: that protocol allowlists `actionId` to exactly
`weather.rangeChanged`, and a tapped surface button sends an
`<assistant-ui-action>` JSON envelope as the user text rather than a plain
member utterance.
```

- [ ] **Step 4: Register the ADR**

`docs/architecture.md` ends with a `## Decision records` section (line 990).
Add a row/bullet for ADR 0013 in the same format the existing entries use,
pointing at `docs/adr/0013-suggestions-are-a-separate-reflex-call.md`.

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0013-suggestions-are-a-separate-reflex-call.md docs/architecture.md
git commit -m "docs(suggest): add ADR 0013 and the architecture section"
```

---

## Final verification

- [ ] `uv run pytest` — the whole unit tier passes.
- [ ] `EVE_LIVE_TESTS=1 uv run pytest -m live -k suggestions` — the live chip test passes.
- [ ] `rg "_last_exchange|_replace_dynamic_tools" src tests` — prints nothing.
- [ ] `rg -n "TAG_NOSTREAM" src/eve/suggest.py` — present.
- [ ] `uv run python scripts/chat.py` against `uv run aegra dev` — reply streams, then chips render.
- [ ] `uv run pytest -m integration` — passes, if docker-compose.test.yml services are available.
