"""Which models a delegated coding session may use.

NOT models.py, and deliberately. That file declares itself "the ONLY place
model identifiers appear", and the invariant exists so retiering EVE is a
one-file change - it is about her five tiers. A delegated coding model is
not a tier: it is an argument to a subprocess, chosen per task, drawn from
a set this repository does not define. A static table here would be a
second list to keep in sync with the proxy, going stale silently.

THE DENY-LIST IS ONE PREFIX, ON A PRINCIPLE. The line is not "which models
work" but HOW THEY FAIL. ADR 0004 probed ocp/* live and found the proxy
strips tool definitions before the model sees them: asked to call a tool,
Claude replies it has no such tool. A coding agent that cannot call tools
answers fluently and changes nothing, for half an hour - undetectable at
runtime, so it is denied here. Models the ChatGPT sign-in refuses fail
loudly at the first prompt with the backend saying why; Eve reports it,
retries, and remembers. Loud failures need no registry.
"""

from __future__ import annotations

import logging
import time

import httpx

from eve.settings import get_settings

logger = logging.getLogger(__name__)

DENIED_PREFIXES: tuple[str, ...] = ("ocp/",)

# The fallback when Eve names nothing usable. Not a catalogue - one name per
# agent, which is what "the agent's own sensible default" costs.
_AGENT_FALLBACK: dict[str, str] = {
    "codex": "chatgpt/gpt-5.6-sol",
    "opencode": "chatgpt/gpt-5.6-sol",
    "claude": "anthropic/claude-sonnet-5",
}

_cache: dict[str, object] = {"models": [], "at": 0.0}


def _reset_cache() -> None:
    _cache["models"] = []
    _cache["at"] = 0.0


async def available_models() -> list[str]:
    settings = get_settings()
    now = time.monotonic()
    if _cache["models"] and now - float(_cache["at"]) < settings.coding_catalogue_ttl_seconds:
        return list(_cache["models"])  # type: ignore[arg-type]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.litellm_base_url}/v1/models",
                headers={"Authorization": f"Bearer {settings.litellm_api_key}"},
            )
            response.raise_for_status()
            body = response.json()
    except Exception:
        logger.warning("could not fetch the LiteLLM catalogue", exc_info=True)
        return []

    models = [
        entry["id"]
        for entry in body.get("data", [])
        if entry.get("id") and not entry["id"].startswith(DENIED_PREFIXES)
    ]
    _cache["models"] = models
    _cache["at"] = now
    return list(models)


async def validate(model: str | None, agent: str) -> str:
    """The model to actually use. Falls back rather than raising: Eve has
    already told the member she is on it, and refusing to start over a
    hallucinated name would spend her credibility on our validation."""
    fallback = _AGENT_FALLBACK.get(agent, _AGENT_FALLBACK["codex"])
    if not model:
        return fallback
    if model.startswith(DENIED_PREFIXES):
        logger.info("model %r is denied; falling back to %r", model, fallback)
        return fallback
    models = await available_models()
    if not models:
        # The proxy is unreachable. Eve's choice is at least as good as ours
        # and a delegation that cannot start is worse than one that might.
        return model
    if model not in models:
        logger.info("model %r is not in the catalogue; falling back to %r", model, fallback)
        return fallback
    return model
