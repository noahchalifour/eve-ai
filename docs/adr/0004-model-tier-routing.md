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
