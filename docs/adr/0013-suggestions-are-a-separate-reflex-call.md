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

The prompt-injection surface is the whole rendered exchange `_render` builds,
not just the memory bundle: the raw human text and Eve's own reply are
inlined too, and Eve's reply can itself carry specialist tool output (an
email body via `ask_mail`, for one). The mitigation is unchanged regardless
of which of those an attacker reaches - a chip is text the member sees and
chooses to send, and ambient-marked turns are skipped outright.

**The feature is invisible to the Flutter client until that client changes**
(Linear OPENA-14). It requests only `messages` and `custom` stream modes,
reads only `values.messages` on restore, and its `custom` handler accepts only
the `assistant_ui` key. The `custom` frame this node emits is dropped on the
floor until then.

Chips are NOT an `assistant-ui/1.0` surface. `actionId` there is allowlisted
to exactly `weather.rangeChanged`, and a tapped surface button sends an
`<assistant-ui-action>` envelope as the user text rather than a plain member
utterance - which is the whole point of a chip.
