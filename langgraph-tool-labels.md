# Handoff: emit `tool_labels` from the LangGraph graph

**For:** an agent working in the LangGraph server repo (the graph behind `agentAssistantId`, reference implementation `eve-ai`).
**Not for:** this Flutter repo. The client half is already merged-ready in PR #22 on `chalifournoah/opena-26-agent-trace-ticker`. Nothing here requires a client change.

This file lives in `scratch/`, which is gitignored (`RULES.md` §3.2). It is a working brief, not a committed contract. The committed contract is `docs/design/decisions/0006-agent-trace-ticker.md` and `docs/internals/services-and-events.md` in this repo.

## Why this exists

The app now renders agent work as a one-line ticker above the answer: a single line naming the current activity while the turn runs, then a timed summary that expands into detail. That line names each tool call.

Without server-supplied labels the client falls back to sentence-casing the raw tool name, so `get_forecast` reads as `Get forecast`. That is accurate but mechanical, and it leaks your function naming into a member-facing conversation. A label lets the graph say `Checking the forecast` instead.

**The fallback is not a failure mode.** Shipping no labels leaves the app working exactly as it does today. This is an enhancement, so partial adoption is fine: label the tools whose names read badly and leave the rest.

## The contract

Write to the `custom` stream channel you already use for `assistant_ui` and `suggestions`. One key, `tool_labels`, mapping raw tool name to a short human-readable activity phrase:

```python
from langgraph.config import get_stream_writer

def call_tools(state):
    writer = get_stream_writer()
    writer({
        "tool_labels": {
            "get_forecast": "Checking the forecast",
            "create_event": "Adding it to your calendar",
        }
    })
    ...
```

The key is the **raw tool name** as it appears in `tool_call_chunks[].name` and `ToolMessage.name`. Not the call id, which is per-invocation; the client maps name to id itself.

### Rules the client enforces

The client re-validates everything, so a malformed frame degrades to "no label" rather than throwing mid-stream or rendering a map into the chat. Emitting something invalid is safe, but it is silently dropped, so match these or the label simply will not appear:

| Rule | Detail |
|---|---|
| Type | `Map<String, String>`. Non-string keys or values are skipped pair by pair. |
| Blank | A blank name or blank label is skipped. Both are trimmed first. |
| Length | A label longer than **60 characters** is rejected outright. |
| Extra keys | Any other key in the `custom` payload is ignored; `tool_labels` coexists with `assistant_ui` and `suggestions` in the same frame or separate ones. |

### Ordering does not matter

This is the part worth getting right, because it is what lets you emit labels wherever it is convenient:

- A label that arrives **before** the tool call rides along with the call when it starts.
- A label that arrives **after** the call has started updates that call in place.
- A label that arrives after a call was announced only by its result also updates in place.

So you can emit one dictionary of every label up front at the top of the graph, or emit each label next to its call, or re-send the same map on every frame. All three work. Re-sending is idempotent from the member's point of view.

Practically: **emit the whole map once, early**. It is the simplest thing that works and avoids any dependency on call timing.

## Writing the labels

The line appears mid-conversation, in the member's reading flow, directly above the answer. So it is product copy, not telemetry.

- **Present participle, describing the action**: `Checking the forecast`, `Adding it to your calendar`, `Searching your documents`.
- **No tool jargon**: not `Invoking get_forecast`, not `Tool: forecast_api`, not `Calling weather service`.
- **Short**: it is one line that truncates with an ellipsis on a phone. Aim for under about 40 characters; 60 is the hard ceiling.
- **No trailing ellipsis**: the client handles progress affordance itself.
- **Sentence case**, no terminal period.
- Specific beats generic when the tool is always specific: `Checking the forecast for Halifax` is better than `Checking the weather` only if the tool genuinely always does that. Labels are static per tool name, so do not write a label that implies arguments it will not always have.

A good test: read it aloud after the words "Right now it is". `Right now it is checking the forecast` should sound like a sentence.

## What you do not need to send

The client deliberately renders none of this, so sending it changes nothing:

- **Tool arguments.** Never rendered, in any form.
- **Structured results.** A map, list, or object result shows as `Completed` or `Failed`.
- **Anything serialized.** A result string opening with `{`, `[`, or `<` is treated as a payload and reduced to a status word, even when it is short enough to pass for a sentence. `{"temp":14}` will not render.
- **Long or multi-line strings.** Over 120 characters, or containing a newline, reduces to a status word.

What *does* render as a result line is a **short, single-line, plain-prose string**. If your tool can cheaply return `Sat 14° clear · Sun 11° rain` instead of a JSON blob, the expanded detail reads much better. That is optional and independent of labels.

## Failure

Tool failure comes from `ToolMessage.status == "error"`, which you are presumably already setting. The client treats anything else, including an absent status, as success.

A failed tool makes the trace **force-expand**, so its detail is visible with no interaction. That makes the failure result string worth writing carefully: `Calendar access denied` is a good one. A stack trace or an error object is not, and will reduce to `Failed`.

If a stream closes after a tool call starts but before its `ToolMessage` arrives, the client shows `Didn't finish` for that call rather than a success check.

## Verifying it

1. Point the app at your deployment: Settings → Agent → endpoint, key, assistant id.
2. Send a prompt that triggers a labelled tool.
3. While the turn runs, the line above the answer should read your label.
4. When it finishes, it becomes `Thought and checked N tools · Ns`. Tap it; each tool row should show your label.

If you see `Get forecast` instead of `Checking the forecast`, the label did not validate. Check length, blankness, and that the key matches the raw tool name exactly.

For a client-side reference of the expected rendering without a server, the mock provider scripts the same shape: any prompt containing `weather` produces a labelled two-tool trace, and adding `denied` fails the second call.

## Pointers into the client

If you need to read the other half of the contract:

- `lib/data/services/agent/langgraph_agent_service.dart`: `_handleCustom` parses the frame, and `_sanitiseToolLabels` is the validation above.
- `lib/ui/features/chat/views/chat_agent_trace.dart`: the ticker, including `outcomeOf` and the payload rejection.
- `test/data/services/agent/langgraph_agent_service_test.dart`: the `custom tool_labels` group covers both orderings and every rejection case. This is the clearest executable spec.
- `docs/internals/services-and-events.md`: the frame-to-event mapping table.

## Scope

In scope: emitting `tool_labels`, and optionally returning prose tool results.

Out of scope: any change to `assistant_ui`, `suggestions`, the openers flow, thread metadata, or message streaming. Those contracts are untouched by this work.
