"""Model tier routing. This module is the ONLY place model identifiers appear.

Retiering Eve - or falling back from the ChatGPT proxy to the Claude proxy -
is a one-file change by construction (spec section 5).

All tiers are served by LiteLLM at `settings.litellm_base_url`. The
`chatgpt/*` models are registered in LiteLLM with `mode: responses`, so the
client is constructed with `use_responses_api=True`; Task 8 verifies that
assumption against the live proxy before any tool work depends on it.
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


# REFLEX is deliberately unmapped: it must run on a metered key rather than a
# subscription proxy (spec section 2.1), and that key is provisioned at the
# start of Phase 2.
TIER_MODELS: dict[Tier, str | None] = {
    Tier.VOICE: "chatgpt/gpt-5.3-chat-latest",
    Tier.DEEP: "chatgpt/gpt-5.4",
    Tier.MECHANICAL: "chatgpt/gpt-5.3-instant",
    Tier.CODE: "chatgpt/gpt-5.3-codex",
    Tier.REFLEX: None,
}


@lru_cache(maxsize=None)
def get_model(tier: Tier) -> BaseChatModel:
    name = TIER_MODELS[tier]
    if name is None:
        raise NotImplementedError(
            f"tier {tier.value!r} is not provisioned until Phase 2"
        )
    settings = get_settings()
    return ChatOpenAI(
        model=name,
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key or "unset",
        use_responses_api=True,
        streaming=True,
    )
