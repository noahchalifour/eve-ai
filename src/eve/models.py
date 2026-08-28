"""Model tier routing. This module is the ONLY place model identifiers appear.

Retiering Eve is a one-file change by construction (spec section 5).

All tiers are served by LiteLLM at `settings.litellm_base_url`. The `chatgpt/*`
models are registered there with `mode: responses`, so the client is built with
`use_responses_api=True`.

WHICH MODELS WORK, AND WHY THESE. Probed against the live proxy on 2026-08-18
(see ADR 0004). Signing in with a ChatGPT account restricts Codex to a specific
model set, and OpenAI renamed that set for the 5.6 generation:

  - `gpt-5.6-sol` / `-terra` / `-luna` are the current ChatGPT-subscription
    models - flagship, balanced, and fast/cheap respectively.
  - The older `chatgpt/*` names are refused by the backend: "The
    \'gpt-5.3-instant\' model is not supported when using Codex with a ChatGPT
    account". `gpt-5.4` still answers but is LEGACY and retires 2026-08-31.
  - Every `ocp/*` Claude model answers and streams, but that proxy strips tool
    definitions before they reach the model - asked to call a tool, Claude
    replies it has no such tool. Conversational only, so `ocp/*` cannot serve
    as a fallback for any tool-using tier. Phase 3 depends on this.

THE FALLBACK IS NOT HERE. It lives in the LiteLLM config (infrastructure repo,
`kubernetes/apps/litellm`): every `chatgpt/*` entry declares `fallbacks` to
`anthropic/claude-sonnet-5`, served by the metered `anthropic_api_key` that
Vault had been holding unused since Phase 1 (ADR 0004 as amended, EVE-2).

Three consequences for anyone reading this file:

  - `TIER_MODELS` names only primaries. A tier does not stop working when its
    entry 404s or rate-limits; LiteLLM swaps the backend and eve never sees it.
    Do not add fallback identifiers here - one owner, and it is the proxy, so
    eve-tools and eve-ambient inherit the same behaviour for free.
  - One fallback serves all four subscription tiers rather than one per tier.
    Degraded mode needs to work, not to preserve tier fidelity, and a single
    entry is a single thing to register, probe, and keep alive.
  - `REFLEX` deliberately has none. It rides the metered Google key, not the
    ChatGPT credential, so it does not share the failure mode this guards
    against; a fallback for it would be speculation.

`use_responses_api` below is the load-bearing detail. LiteLLM translates a
Responses-API request onto Anthropic's Messages API rather than passing it
through, so the flag stays keyed to the PRIMARY's prefix and a fallback hop is
invisible to this client. That translation is verified live, not assumed -
tests/test_live_models.py::test_fallback_model_emits_tool_calls exists because
ADR 0004's original fallback plan died on exactly this assumption.

"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from eve.settings import get_settings


class Tier(str, Enum):
    VOICE = "voice"           # Eve herself
    DEEP = "deep"             # planning, hard reasoning (Phase 5)
    MECHANICAL = "mechanical" # structured specialist work (Phase 3)
    CODE = "code"             # authoring skills and tool code (Phase 5)
    REFLEX = "reflex"         # ambient filtering, memory extraction (Phase 2)


TIER_MODELS: dict[Tier, str] = {
    Tier.VOICE: "chatgpt/gpt-5.6-terra",
    Tier.DEEP: "chatgpt/gpt-5.6-sol",
    Tier.MECHANICAL: "chatgpt/gpt-5.6-luna",
    Tier.CODE: "chatgpt/gpt-5.6-sol",
    # Metered Google key, NOT the ChatGPT subscription proxy: this tier runs
    # on every turn (extraction) and in Phase 4 on every household signal, and
    # must not consume the rate limits Noah uses for his own work (spec 2.1).
    Tier.REFLEX: "gemini/gemini-flash-lite-latest",
}


@lru_cache(maxsize=None)
def get_model(tier: Tier) -> BaseChatModel:
    settings = get_settings()
    return ChatOpenAI(
        model=TIER_MODELS[tier],
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key or "unset",
        # The chatgpt/* models are registered with `mode: responses`. Gemini
        # is not, and sending it a Responses-API request fails.
        use_responses_api=TIER_MODELS[tier].startswith("chatgpt/"),
        streaming=True,
    )
