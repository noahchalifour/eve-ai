# Eve — Reply suggestions ("chips") — Design

**Date:** 2026-08-31
**Status:** Approved, not yet implemented.
**Issue:** [EVE-7](https://linear.app/chalifour-development/issue/EVE-7/add-suggestions-generation)

**Scope of this document:** the eve-ai (server) side of member-facing reply
suggestions only. Graph mechanics, tiers and the tools loop are assumed from
[`2026-08-17-eve-core-design.md`](2026-08-17-eve-core-design.md); memory recall
and extraction from [`2026-08-18-eve-memory-design.md`](2026-08-18-eve-memory-design.md);
ambient signal handling from [`2026-08-23-eve-ambient-design.md`](2026-08-23-eve-ambient-design.md).

**Delivers:** after Eve answers, 2–4 short things the *member* could say next,
written in the member's own voice, delivered to the client so it can render
them as tappable chips.

**Does NOT deliver:** the client-side rendering. See section 8 — the Flutter
client cannot see this feature without a change in its own repo, tracked as a
separate linked issue.

---

## 1. What a suggestion is

A suggestion is a candidate **next utterance by the member**, not by Eve.
First person, short, tappable verbatim: "Yes, do it", "What about tomorrow?",
"Only the kitchen ones".

The consequence that keeps the contract small: a chip is just text the member
might have typed, so tapping one produces an ordinary `HumanMessage` and needs
no new inbound protocol at all. The wire type is `list[str]`. No chip carries
an id, a type, or an action.

Deliberately excluded: action-typed chips ("turn them all off" as a command
rather than an utterance). Every such chip is an utterance Eve would route to a
specialist anyway, and typing it would mean teaching the client a richer chip
schema for no behavioural difference.

---

## 2. Why a separate REFLEX call, and not part of Eve's own turn

The tempting alternative is to have the `eve` node emit reply-plus-chips in one
structured call: zero extra latency, zero extra API call. It was considered
seriously and rejected. Recorded here and in ADR 0013 because it is the first
thing any future reader will propose.

One objection commonly raised against it is **wrong** and should not be
recycled: structured output does *not* preclude binding real tools.
`with_structured_output(schema, tools=[...], strict=True, include_raw=True)` is
supported by langchain-openai ≥ 0.3.12 and returns `{"raw", "parsed",
"parsing_error"}`, with tool calls arriving on `raw`. Specialists and dynamic
skills would still work.

The reasons that do hold, in descending weight:

1. **It breaks the streaming contract for every client, permanently.** With
   `method="json_schema"` the model's *text* output is the JSON, so what
   LangGraph puts on the `messages` channel — and Aegra forwards over SSE — is
   `{"reply":"Hi No` fragments. Every client would have to incrementally
   partial-parse JSON to render prose. The Flutter client renders
   `messages/partial` deltas directly
   (`lib/data/services/agent/langgraph_agent_service.dart:408-430`); so does
   `scripts/chat.py`. That is a permanent tax on every present and future
   client to save one cheap call.
2. **It forces two representations of one message.** History has to stay prose,
   because `eve/memory/extract.py:154` and `:300` and `eve/eval/replay.py:106`
   all read `AIMessage.content` as prose — left as JSON, memory extraction
   would mine facts out of a JSON blob, the six-turn digest transcript would
   become JSON, and eval scoring would score JSON. Storing
   `AIMessage(parsed["reply"])` fixes history but makes the streamed form and
   the stored form differ, so a client rebuilding a thread from
   `GET /threads/{id}/state` sees something other than what it streamed.
3. **The fallback path is unverified and historically fragile.** VOICE is
   `chatgpt/gpt-5.6-terra` with `use_responses_api=True`, falling back through
   LiteLLM to `anthropic/claude-sonnet-5`. This would ask LiteLLM to translate
   a Responses-API `text.format` json_schema onto Anthropic's Messages API —
   the same class of assumption that killed ADR 0004's first fallback plan, and
   the reason `tests/test_live_models.py::test_fallback_model_emits_tool_calls`
   exists. Answerable only by a live probe, and not worth standing one up for
   this.
4. **It adds a third outcome to the hottest node.** `eve` is a cycle; on
   tool-calling rounds `parsed` is `None` by design, so the node would branch
   on tool-calls / valid-parse / parse-error on every turn forever.
5. **Cost inversion.** Chips billed at VOICE on every turn, with the schema
   re-sent on every intermediate tool round, versus one
   `gemini-flash-lite-latest` call.

Also rejected: **prose followed by a delimited chip block**. It preserves
streaming and needs no schema, but has no format enforcement, requires every
client to buffer the reply tail to avoid flashing the delimiter, is still
VOICE-priced, and still leaves the block in stored history.

Also rejected: **generating chips in a detached background task**, the ADR 0012
pattern. By the time such a task finishes the graph has reached `END` and the
SSE stream is closed. Writing the result into thread state afterwards would
need the checkpointer, which `eve`'s graph deliberately does not own
(`graph.py` compiles without one so Aegra's Postgres persistence is not
shadowed); it is reachable via
`config["configurable"]["__pregel_checkpointer"]`, but that is private
LangGraph internals and writing a checkpoint outside a run is off-contract.
Stashing it in eve's own Postgres has nothing to serve it, because Aegra owns
the app — which would mean a bespoke endpoint, exactly what EVE-7's "supported
in langgraph API" rules out. Every variant ends with the client polling, which
is worse than the accepted cost below.

**Accepted cost:** the run stays open for one REFLEX call after the last token.
This is deliberately the thing ADR 0012 moved extraction out of the graph to
avoid, and the distinction is that ADR 0012's complaint was the turn not
looking finished while doing work *the client did not need*. Chips are work the
client does need; they cannot render before they exist under any design.
Section 4's budget bounds the exposure.

---

## 3. Placement in the graph

```
eve --(no tool calls)--> extract --> suggest --> END
```

`suggest` goes **after** `extract`, and the ordering is load-bearing. With
`EVE_MEMORY_EXTRACT_BACKGROUND=true` (the default), `extract` registers its
task and returns immediately (ADR 0012), so extraction's REFLEX call and
`suggest`'s REFLEX call overlap: the turn pays `max()` of the two, not `sum()`.
Placing `suggest` first would serialise them for no gain.

With `EVE_MEMORY_EXTRACT_BACKGROUND=false` they serialise. That flag is a
debugging escape hatch and the extra latency there is acceptable.

Skip logic lives **inside** the node rather than in a conditional edge. A
router would add a branch to the graph shape to express something the node has
to check anyway, and `builder.add_conditional_edges("eve", tools_condition,
...)` already carries the only routing decision that changes the turn's path.

`build_graph` grows a `suggest_fn` parameter alongside the existing
`recall_fn` / `extract_fn` seam, so tests and `eval/replay.py` can inject a
no-op.

---

## 4. The node — `src/eve/suggest.py`

One REFLEX-tier call through `with_structured_output`, following
`eve_ambient/filter.py`'s three-outcome discipline, which exists precisely
because these causes must not collapse into each other:

- **transient failure** (connection, timeout, HTTP error) → `[]`
- **malformed structured response** (`OutputParserException`,
  `ValidationError`) → `[]`
- **budget exceeded** → `[]`

All three degrade to no chips. None raises. A member never loses a reply
because chip generation had a bad day, and a turn never hangs on it.

**Budget.** The call is wrapped in `asyncio.timeout(suggest_budget_ms / 1000)`,
default 1500ms, the same idiom as `recall`'s embedding arm
(`memory/recall.py:58-61`). Measured from task creation, not from await, for
the reason documented at `memory/recall.py:87-91`.

**Skips**, each returning `[]` rather than `{}` — see section 5 for why that
distinction matters:

| Condition | Why |
|---|---|
| `is_ambient_text(last_human)` | An ambient turn is not a member speaking, and its reply goes to ntfy push, not a chat surface. Also saves a REFLEX call per household signal. Reuses the existing guard rather than a second copy (`state.py`). |
| Reply is `_LOOP_EXHAUSTED` | The tools loop gave up. That is not a conversation to offer continuations of. |
| `suggest_enabled` is false | Kill switch. |

**Model input.** Last human message plus last AI message. `memory/extract.py`
already has this logic as a module-private `_last_exchange`
(`extract.py:150`); it is renamed to `last_exchange` and imported here rather
than copied, since two readers of "the last exchange" that can drift is worse
than one shared name. Its two existing callers are `extract.py:229` and a
docstring reference in `tests/test_memory_extract.py:433`. Plus the
member's name and role from `state["member"]`; and the rendered memory bundle
`recall` already placed in state, so chips can reflect what Eve knows about the
member. Prompt at `prompts/suggest.md`, loaded with the established
`lru_cache` + `settings.prompt_file.parent` pattern
(`eve_ambient/filter.py:47-49`).

**Output validation is strict**, because chips are rendered verbatim by a
client: capped at 4, each trimmed, and any empty or over-long (> 80 chars)
entry dropped. A model that returns eight chips or a paragraph produces a
short valid list, not a broken UI.

There is **no minimum**. The prompt asks for 2-4, but a response validating to
one good chip ships that one chip. Enforcing a floor would mean either
discarding a usable suggestion or retrying the call inside a budget that exists
to bound the turn - both worse than one chip.

**Prompt-injection note.** The rendered memory bundle reaches this prompt, and
memory content can originate from text Eve was told. A poisoned memory could
therefore shape a chip. The blast radius is small by construction: a chip is
text the *member* chooses to send, shown to them before it is sent, and
ambient-marked turns — the untrusted-input path — are skipped outright. No
additional guard beyond that skip.

---

## 5. State, and why the reducer is not optional

```python
suggestions: Annotated[list[str], _last_write_wins]
```

The reducer is mandatory, for the reason documented at `graph.py:106`: every
field of `EveState` is a *required* field of the pydantic schema
`InjectedState` validates, and a channel with no reducer gets `LastValue`,
which holds no value at all until something writes it. On a fresh thread the
key would simply be absent and every tool taking
`Annotated[EveState, InjectedState]` would fail validation before its body
ran — the same failure mode `_replace_dynamic_tools` was written to prevent.

`_replace_dynamic_tools` is renamed to `_last_write_wins` and both channels
point at it. It is literally the same function, and this codebase prefers one
owner for a shared behaviour. Two comments name it by its old name and need
updating: `graph.py:106` and `eval/replay.py:73`. No test references it
(verified).

Last-write-wins is also why every skip path returns `[]` rather than `{}`. A
skipped turn that writes nothing leaves the *previous* turn's chips in state,
so a client would render stale chips against a fresh reply — chips that were
plausible continuations of a conversation that has since moved on.

---

## 6. Delivery

Two exits, one owner. The node computes the list once and hands it to a single
helper that both emits it and returns it, so the two paths cannot drift.

**1. `custom` stream frame — what the real client consumes.**

```python
get_stream_writer()({"suggestions": [...]})
```

Called unconditionally, no guard, but two distinct cases sit behind that:
inside a real graph node with no `custom` stream consumer, `stream_writer`
defaults to `_no_op_stream_writer` (`langgraph/runtime.py:206,288`), so the
call is inert. Called outside a runnable context - e.g. a direct
`await suggest(...)` in a unit test - `get_stream_writer()` instead raises
`RuntimeError`, since it calls `get_config()` internally; `_emit` catches
that specifically (logged at `debug`, not `warning`) and still returns the
state-channel value below. Either way the node completes and eval replay
sees no side effect. Aegra forwards the `custom` channel
(`aegra_api/services/event_streaming/protocol.py:23`, `session.py:218,346`).

**2. `suggestions` state channel — the stock LangGraph contract.**

Serves `GET /threads/{id}/state` after the run, `stream_mode="values"` and
`"updates"` for any stock-SDK client, and inspection during development. It is
also what makes chips survive a reload once a client reads it, because `custom`
frames are streamed and never stored.

Nothing reads the state channel today, so it is close to speculative — kept
because it is one dict key in the node's return value, it is the answer to
"supported in langgraph API" as EVE-7 asked, and a `custom`-only design would
make `scripts/chat.py` consume a stream mode it otherwise has no use for.

`scripts/chat.py` is updated to render chips, since it is the only client in
this repo and proves the contract end to end.

---

## 7. Settings, eval, observability

**Settings** (prefix `EVE_`, per `settings.py:16-18`):

| Setting | Default | Notes |
|---|---|---|
| `suggest_enabled` | `true` | Unlike `ambient_enabled` and `sandbox_enabled`, this subsystem reaches nothing outside the process and writes nothing durable, so default-on is safe. |
| `suggest_budget_ms` | `1500` | Section 4. |

**Eval.** `eval/replay.py` passes a no-op `suggest_fn`, exactly as it already
does for `extract_fn` (`replay.py:88-90`). Eval runs therefore do not pay for
chips, no dataset shape changes, and `voice_call_estimate` stays correct
because this tier is REFLEX, not VOICE.

**Observability**, in `recall`'s attribute style (`memory/recall.py:192-212`):

- `eve.suggest.outcome` — `ok` / `empty` / `budget` / `malformed` / `error` /
  `skipped` / `disabled`
- `eve.suggest.count`
- `eve.suggest.latency_ms`

`outcome` is the named signal for "chips silently stopped firing", which is
precisely the failure mode section 4's degradation makes otherwise invisible.

---

## 8. What this does not do: the client

The Flutter client at `~/GitHub/open-assistant/flutter-open-assistant` cannot
see this feature as shipped. Established by reading its source:

- It requests `stream_mode: ['messages', 'custom']` only —  no `values`, no
  `updates` (`lib/data/services/agent/langgraph_agent_service.dart:308-322`).
- On restore it reads `values.messages` and no other channel
  (`langgraph_agent_service.dart:206-212, 245-272`).
- Its `custom` handler accepts **only** the `assistant_ui` key and silently
  drops any other (`langgraph_agent_service.dart:432-450`).
- `AgentEvent` has no suggestion variant
  (`lib/domain/models/agents/agent_event.dart:7-61`).

So the `custom` frame this design emits is dropped on the floor until the
client changes. That change is small, because the UI already exists: a rail of
tappable pills above the composer, rendered when a thread has messages, fed
today by a hard-coded Dart `const kChatSuggestions`
(`lib/ui/features/chat/views/chat_suggestion_rail.dart:10`,
`mobile_chat_screen.dart:183-226`). Server-driven chips replace that constant's
contents. Note that tapping currently only *prefills* the composer
(`mobile_chat_screen.dart:51-56`); whether a server-suggested chip should send
immediately is a client-side product decision, not this design's.

Tracked as a separate linked Linear issue against the client repo, following
how EVE-12 is structured. One spec per repo.

**Why chips are not an `assistant-ui/1.0` surface.** The catalog has `button`
and `segmentedSelection`, and an inbound `<assistant-ui-action>` envelope, so a
row of buttons is structurally renderable. Three reasons not to use it: `actionId`
is allowlisted to exactly `weather.rangeChanged`
(`lib/data/services/agent/dynamic_surface_protocol.dart:254-255`), documented as
deliberate in that repo's `docs/internals/dynamic-chat-ui.md:167-170` — "a
provider cannot invent new remote action ids" — and relaxing it means changing
three files kept in lockstep by hand; EVE-12's `src/eve/ui/` is not implemented
yet, so this would depend on unbuilt work; and semantically a tapped surface
button sends an `<assistant-ui-action>` JSON envelope as the user text, not
"Yes, do it" as a member utterance, which is the whole point of section 1.

---

## 9. Testing

`tests/test_suggest.py`, one test per outcome so a regression names its own
cause:

- valid response → chips returned, capped at 4
- over-long, empty and whitespace-only entries dropped
- more than four returned → truncated
- malformed structured response → `[]`, outcome `malformed`
- transient call failure → `[]`, outcome `error`
- budget exceeded → `[]`, outcome `budget`
- ambient-marked turn → `[]`, no model call made
- `_LOOP_EXHAUSTED` reply → `[]`, no model call made
- `suggest_enabled=false` → `[]`, no model call made
- `custom` frame emitted with the same list the node returns
- node is safe under `ainvoke` with no `custom` stream mode (no-op writer)

`tests/test_graph.py`:

- `suggest` runs after `extract` and before `END`
- a turn's chips appear in final state
- a skipped turn clears the previous turn's chips rather than leaving them

One live test in `tests/test_live_models.py`'s style: a real REFLEX call on a
real exchange returns 2–4 non-empty chips within budget. This repo verifies
model behaviour live rather than assuming it (ADR 0004's history), and chip
quality is exactly the kind of thing a mock cannot fail on.

---

## 10. Documentation

- **ADR 0013** — "Reply suggestions are a separate REFLEX call". Section 2's
  decision, including the explicitly-not-a-reason note about tool coexistence,
  so the rejected alternative is not re-proposed on a bad premise.
- `docs/architecture.md` — a section for the node, its settings, and its
  degradation behaviour.
- A linked Linear issue against `flutter-open-assistant` for section 8.
