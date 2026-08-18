# 4. Model tier routing

**Status:** Accepted
**Date:** 2026-08-17

## Context

LiteLLM fronts two subscription proxies (ChatGPT and OCP for Claude) and no
metered general-purpose API. Eve should draw on the ChatGPT subscription.
The `chatgpt/*` models are registered with `mode: responses`, and
subscription proxies are the component most likely to support function
calling only partially - which Phase 3 depends on entirely.

## Decision

Tiers map to `chatgpt/*` models, defined only in `src/eve/models.py`. Each
declares a LiteLLM fallback to its OCP Claude equivalent. `REFLEX` stays
unmapped until Phase 2 provisions a metered key, because its volume would
otherwise exhaust subscription rate limits.

## Verification

`tests/test_live_models.py` checks response shape, token streaming, and tool
calling against the live proxy.

**Status as of 2026-08-17: written, not yet run.** The implementing agent
does not hold a LiteLLM virtual key for `litellm.chalifour.dev`, so the
suite could not be executed to completion. One attempted run was made to
confirm the harness itself is sound:

```bash
EVE_LIVE_TESTS=1 uv run pytest tests/test_live_models.py -v -m live
```

All three tests failed identically, at the transport layer, before any
response-shape or tool-calling assertion was reached:

```
openai.AuthenticationError: Error code: 401 - {'error': {'message':
"Authentication Error, LiteLLM Virtual Key expected. Received=****,
expected to start with 'sk-'.", 'type': 'auth_error', 'param': 'None',
'code': '401'}}
```

This confirms the test harness constructs and sends the request correctly
and reaches the proxy; it says nothing about whether `use_responses_api`
or tool calling work, because a real virtual key never reached the model.

**The command the user must run to retire this risk:**

```bash
EVE_LIVE_TESTS=1 EVE_LITELLM_API_KEY=<eve virtual key> \
  uv run pytest tests/test_live_models.py -v -m live
```

**What each outcome means:**

- **All three pass:** both proxy assumptions hold. No further action; this
  section should be updated to record the pass.
- **`test_voice_tier_responds_through_litellm` (and/or
  `test_voice_tier_streams_tokens`) fails with a request-shape error:**
  `use_responses_api=True` in `src/eve/models.py` is wrong for this proxy.
  Flip it to `False`, rerun, and record which setting works here.
- **`test_voice_tier_emits_tool_calls` fails** (the other two pass): the
  ChatGPT proxy does not reliably return tool calls. Change `TIER_MODELS`
  in `src/eve/models.py` to the OCP Claude equivalents
  (`ocp/claude-sonnet-5`, `ocp/claude-opus-5`, `ocp/claude-haiku-4-5`,
  `ocp/claude-haiku-4-5`), rerun, and record the reversal here. No other
  file changes are required for that fallback, by construction.

## Consequences

If the proxy proves unreliable, reverting to OCP Claude is a one-file change,
which is why `models.py` is the sole owner of model identifiers.
