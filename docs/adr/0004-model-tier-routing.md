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

**Run on 2026-08-18. The tier table was wrong, and the finding is load-bearing
for Phase 3.**

Probing every model `litellm.chalifour.dev` serves produced:

| Model | Answers | Streams | Tool calls |
|---|---|---|---|
| `chatgpt/gpt-5.4` | yes | 13 chunks | **yes** |
| `chatgpt/gpt-5.4-pro`, `gpt-5.3-codex`, `-codex-spark`, `-instant`, `-chat-latest` | refused | — | — |
| `ocp/claude-sonnet-5`, `ocp/claude-haiku-4-5` | yes | 3 chunks | **no** |

Two things this settled:

1. **The ChatGPT credential is a ChatGPT-account Codex sign-in**, which serves
   a restricted model set. The refusals are all *"The '<model>' model is not
   supported when using Codex with a ChatGPT account"* — including
   `gpt-5.3-codex`. OpenAI renamed that set for the 5.6 generation, and
   `gpt-5.4` is legacy with a **2026-08-31 retirement date**. The tiers
   therefore now target `gpt-5.6-sol` / `-terra` / `-luna`, registered in
   LiteLLM the same day (infrastructure repo, `kubernetes/apps/litellm`).
   Those three are documented as available to ChatGPT sign-in but could not be
   probed before registration; **confirm them after that change rolls out.**

2. **`ocp/*` cannot do tool calling at all.** Not "declined to call" — the
   proxy strips tool definitions before the model sees them, and Claude replies
   that it has no such tool. `use_responses_api=True` also 404s against OCP, so
   it is Chat Completions only.

Consequence for the fallback design: **the OCP fallback chain this ADR assumed
does not work for any tool-using tier.** Falling back from a `chatgpt/*` model
to `ocp/claude-*` would silently produce an agent that cannot call tools, which
in Phase 3 means a specialist that cannot act while still answering fluently —
the worst failure mode available. No tool-capable fallback currently exists in
the instance. Vault holds an unused `anthropic_api_key`; wiring it into LiteLLM
is the cheapest way to get one, and should happen before Phase 3.

Response shape: `use_responses_api=True` is correct for `chatgpt/*` and wrong
for `ocp/*`.

## Consequences

If the proxy proves unreliable, reverting to OCP Claude is a one-file change,
which is why `models.py` is the sole owner of model identifiers.

## Amendment (2026-08-28, EVE-2)

The unused `anthropic_api_key` this ADR flagged is now wired into LiteLLM as
`anthropic/claude-sonnet-5`. Every `chatgpt/*` model entry declares it as a
`fallbacks` target (infrastructure repo, `kubernetes/apps/litellm`) - one
fallback for all four subscription tiers, not a per-tier OCP-style matrix.
Degraded mode needs to work, not preserve tier fidelity, and this credential
is independent of both the ChatGPT sign-in and the Gemini key REFLEX/embedding
already depend on, so it doesn't share either's failure mode. `REFLEX` still
gets no fallback, for the same reason it already stands apart.

This is proxy-side config, not a `models.py` change: `TIER_MODELS` continues
to name only primaries, and eve, eve-tools, and eve-ambient all inherit the
fallback for free by asking LiteLLM for the same model names as before.

The one thing this amendment does not get to assume: that LiteLLM translates
a Responses-API request onto the fallback's Messages API correctly. That is
exactly the kind of untested API-shape assumption that sank the original OCP
fallback plan above, so it is probed live, not inferred -
`tests/test_live_models.py::test_fallback_model_emits_tool_calls`.

## Amendment (2026-09-01, EVE-4)

Delegated coding sessions are a new and much heavier consumer of the same
ChatGPT subscription this ADR routes the tiers through. Three things follow.

**Codex through LiteLLM still rides the subscription.** LiteLLM fronts the
ChatGPT/Codex sign-in itself, so pointing `codex-acp` at the proxy keeps
eve-computer's zero-metered-spend property rather than abandoning it. Same
for OpenCode on a `chatgpt/*` model. The one agent with real metered spend
is Claude Code, on `anthropic/claude-sonnet-5`. Codex is therefore the
tiebreak agent when nothing else points anywhere.

**Rate limits, not dollars, are the thing to watch.** `REFLEX` was moved off
this credential precisely so it would not consume the limits Noah uses for
his own work. A coding agent running for half an hour is a far heavier
consumer than any chat turn. The session bounds are the throttle.

**The refused model set is not enumerated anywhere in the repository.** The
probe above found `gpt-5.3-codex`, `-codex-spark`, `-instant`,
`-chat-latest`, and `gpt-5.4-pro` all rejected by the sign-in, and LiteLLM
still lists them - so Eve can pick one and it will fail. That is accepted:
those failures are LOUD, at the first prompt, with the backend saying why.
Only `ocp/*` is denied (`eve/coding/catalogue.py`), because it fails
SILENTLY - the proxy strips tool definitions, and a coding agent that cannot
call tools answers fluently and changes nothing. Loud failures need no
registry; silent ones do.
