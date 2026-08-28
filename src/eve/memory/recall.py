"""The `recall` node.

The lexical arm fires immediately and cannot fail. The vector arm races a
budget and is fused in only if it lands. A degraded turn is a complete turn:
the always-on layers are untouched and episodic falls back to a real lexical
ranking rather than to nothing. There is no path where Gemini being slow
makes Eve amnesiac - only slightly worse at paraphrase, for one turn.

This is the one place ADR 0002 bends, and it bends by exactly one bounded,
cancellable embedding call.
"""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from opentelemetry import trace

from eve.memory.embed import embed_query
from eve.memory.ranking import fit_budget, fuse
from eve.memory.store import (
    load_always_on,
    search_episodic_lexical,
    search_episodic_vector,
)
from eve.memory.types import Memory, MemoryBundle
from eve.settings import get_settings

logger = logging.getLogger(__name__)

_CANDIDATES = 20
# Set by tests to shrink the race window. Production reads settings.
EMBED_BUDGET_OVERRIDE_S: float | None = None


def _last_human_text(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _budget_seconds() -> float:
    if EMBED_BUDGET_OVERRIDE_S is not None:
        return EMBED_BUDGET_OVERRIDE_S
    return get_settings().memory_recall_embed_budget_ms / 1000.0


async def _embed_within_budget(query: str) -> list[float]:
    """Bound the embedding from task creation, not from when it is awaited."""
    async with asyncio.timeout(_budget_seconds()):
        return await embed_query(query)


async def _cancel_and_await(task: asyncio.Task[object]) -> None:
    """Leave no detached embedding task without swallowing cancellation."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():
            raise
    except Exception:
        pass


async def recall(state: dict, config: RunnableConfig) -> dict:
    started = perf_counter()
    settings = get_settings()
    sub = state["member"]["sub"]
    thread_id = config.get("configurable", {}).get("thread_id")
    query = _last_human_text(state["messages"])

    # Start the clock on the embedding BEFORE the lexical query, so the two
    # overlap. The lexical round trip is a few milliseconds of the budget the
    # embedding would otherwise have had entirely to itself.
    embed_task = (
        asyncio.create_task(_embed_within_budget(query))
        if query.strip()
        else None
    )

    try:
        profile, household, digest, rules = await load_always_on(
            sub, thread_id, include_rules=settings.self_authoring_enabled
        )
        lexical = (
            await search_episodic_lexical(sub, query, limit=_CANDIDATES)
            if query.strip()
            else []
        )

        episodic: list[Memory] = lexical
        vector_used = False
        if embed_task is not None:
            try:
                embedding = await embed_task
            except Exception:
                # Timeout, transport error, a zero vector - all the same response.
                logger.debug("recall: vector arm missed its budget", exc_info=True)
            else:
                vectors = await search_episodic_vector(
                    sub, embedding, limit=_CANDIDATES
                )
                episodic = _fuse_memories(lexical, vectors)
                vector_used = True

        # A four-way split when Phase 5a is on. Rules are usually few and
        # short, so an equal share overpays them slightly and costs nothing
        # when the layer is empty - whatever the always-on layers do not spend
        # still flows to episodic below. With the setting off there is no rule
        # arm to pay for, and the split stays exactly Phase 4's three ways:
        # a deployment that never touches this feature must not have
        # profile/household quietly shrink by a quarter.
        share = settings.memory_token_budget // (
            4 if settings.self_authoring_enabled else 3
        )
        profile = fit_budget(profile, share)
        household = fit_budget(household, share)
        rules = fit_budget(rules, share)
        # Whatever the always-on layers did not spend flows to episodic, which is
        # the only unbounded layer and so the only one that can use it.
        spent = sum(len(m.content) // 4 for m in (*profile, *household, *rules))
        episodic = fit_budget(episodic, settings.memory_token_budget - spent)

        latency_ms = (perf_counter() - started) * 1000
        _record_span(profile, household, episodic, rules, vector_used, latency_ms)

        return {
            "memory": MemoryBundle(
                profile=profile,
                household=household,
                episodic=episodic,
                rules=rules,
                digest=digest,
                vector_used=vector_used,
                latency_ms=latency_ms,
            )
        }
    finally:
        if embed_task is not None:
            await _cancel_and_await(embed_task)


def _fuse_memories(lexical: list[Memory], vectors: list[Memory]) -> list[Memory]:
    by_id = {m.id: m for m in (*lexical, *vectors)}
    order = fuse([m.id for m in lexical], [m.id for m in vectors])
    return [by_id[i] for i in order]


def _record_span(
    profile: list[Memory],
    household: list[Memory],
    episodic: list[Memory],
    rules: list[Memory],
    vector_used: bool,
    latency_ms: float,
) -> None:
    """Whether the 120ms budget actually holds is a number in Langfuse, not
    an assumption. If the degrade rate turns out to be high, the honest
    response might be to drop the vector arm entirely - and that is a
    decision this attribute makes possible."""
    span = trace.get_current_span()
    span.set_attribute("eve.recall.vector_used", vector_used)
    span.set_attribute("eve.recall.latency_ms", round(latency_ms, 1))
    span.set_attribute(
        "eve.recall.items",
        len(profile) + len(household) + len(episodic) + len(rules),
    )
    # How much of the prompt budget Eve's own rules actually consume. Design
    # doc section 9: the plausible failure is that authoring never fires, and
    # this number staying at zero is how that is detected.
    span.set_attribute("eve.recall.rules", len(rules))
    span.set_attribute(
        "eve.recall.tokens",
        sum(
            len(m.content) // 4
            for m in (*profile, *household, *episodic, *rules)
        ),
    )
