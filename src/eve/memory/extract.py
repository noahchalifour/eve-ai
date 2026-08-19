"""Post-stream memory extraction and durable-memory writes."""

from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from opentelemetry import trace

from eve.memory.embed import embed_texts
from eve.memory.store import (
    add,
    evict_over_cap,
    forget,
    overlapping,
    reinforce,
    set_embeddings,
    subjects_in,
    supersede,
    upsert_digest,
)
from eve.memory.types import Extraction, Operation
from eve.models import Tier, get_model
from eve.settings import get_settings

logger = logging.getLogger(__name__)

_CAPPED = {"profile": "memory_profile_cap", "household": "memory_household_cap"}


@lru_cache(maxsize=1)
def load_extract_prompt() -> str:
    return (get_settings().prompt_file.parent / "extract.md").read_text()


def _resolve_scope(op: Operation, member: dict) -> tuple[str, str, str]:
    """Resolve layer and scope after enforcing shared-write permission."""
    if op.layer == "household":
        if "memory.write_shared" in (member.get("permissions") or []):
            return "household", "household", ""
        return "profile", "member", member["sub"]
    return op.layer or "episodic", "member", member["sub"]


def _is_single_sentence(content: str) -> bool:
    """Accept one non-empty, terminally punctuated sentence and nothing else."""
    trimmed = content.strip()
    return (
        bool(trimmed)
        and trimmed[-1] in ".!?"
        and bool(trimmed[:-1].strip())
        and not any(mark in trimmed[:-1] for mark in ".!?")
    )


async def apply_operations(
    operations: list[Operation], member: dict, thread_id: str | None, run_id: str | None
) -> dict[str, int]:
    """Apply model operations, retaining only writes valid at this boundary."""
    counts: dict[str, int] = {}
    last_added: str | None = None
    to_embed: list[tuple[str, str]] = []
    touched_scopes: set[tuple[str, str, str]] = set()

    for op in operations:
        if op.op == "add":
            if not op.content or not _is_single_sentence(op.content):
                continue
            layer, scope_kind, scope_id = _resolve_scope(op, member)
            subject = op.subject.strip().lower() if op.subject else None
            last_added = await add(
                layer=layer,
                scope_kind=scope_kind,
                scope_id=scope_id,
                kind=op.kind or "fact",
                content=op.content,
                subject=subject or None,
                source_thread=thread_id,
                source_run=run_id,
            )
            if layer == "episodic":
                to_embed.append((last_added, op.content))
            if layer in _CAPPED:
                touched_scopes.add((layer, scope_kind, scope_id))
        elif op.op == "supersede":
            if not op.target_id:
                continue
            await supersede(op.target_id, last_added, "contradicted")
        elif op.op == "reinforce":
            if not op.target_id:
                continue
            await reinforce(op.target_id)
        elif op.op == "forget":
            if not op.target_id:
                continue
            await forget(op.target_id)
        else:
            continue
        counts[op.op] = counts.get(op.op, 0) + 1

    if to_embed:
        vectors = await embed_texts([content for _, content in to_embed])
        await set_embeddings(
            [(mid, vec) for (mid, _), vec in zip(to_embed, vectors, strict=True)]
        )

    settings = get_settings()
    for layer, scope_kind, scope_id in touched_scopes:
        evicted = await evict_over_cap(
            layer, scope_kind, scope_id, getattr(settings, _CAPPED[layer])
        )
        if evicted:
            counts["evict"] = counts.get("evict", 0) + evicted

    return counts


def _render_candidates(memories: list) -> str:
    if not memories:
        return "(none)"
    return "\n".join(
        f"- id={m.id} layer={m.layer} subject={m.subject or '-'}: {m.content}"
        for m in memories
    )


def _last_exchange(messages: list) -> tuple[str, str]:
    human = next(
        (m.content for m in reversed(messages) if isinstance(m, HumanMessage)), ""
    )
    ai = next((m.content for m in reversed(messages) if isinstance(m, AIMessage)), "")
    return str(human), str(ai)


async def extract(state: dict, config: RunnableConfig) -> dict:
    """Extract memory after Eve's answer without allowing failures to fail a turn."""
    member = state["member"]
    thread_id = config.get("configurable", {}).get("thread_id")
    run_id = config.get("configurable", {}).get("run_id")
    human, ai = _last_exchange(state["messages"])
    if not human:
        return {}

    try:
        candidates = await overlapping(
            member["sub"], subjects_in(human), None, limit=10
        )
        prompt = (
            f"{load_extract_prompt()}\n\n"
            f"## Existing memories that may overlap\n{_render_candidates(candidates)}\n\n"
            f"## The exchange\n{member['name']}: {human}\nEve: {ai}\n"
        )
        model = get_model(Tier.REFLEX).with_structured_output(Extraction)
        result = await model.ainvoke([HumanMessage(prompt)])
        counts = await apply_operations(
            list(result.operations), member, thread_id, run_id
        )
    except Exception:
        logger.warning("extraction failed for thread %s", thread_id, exc_info=True)
        trace.get_current_span().set_attribute("eve.extract.failed", True)
        return {}

    span = trace.get_current_span()
    for op_name in ("add", "supersede", "reinforce", "forget", "evict"):
        span.set_attribute(f"eve.extract.ops.{op_name}", counts.get(op_name, 0))

    await _maybe_refresh_digest(state, thread_id)
    return {}


async def _maybe_refresh_digest(state: dict, thread_id: str | None) -> None:
    """Refresh a thread digest on the configured cadence."""
    try:
        if not thread_id:
            return
        settings = get_settings()
        cadence = settings.memory_digest_every_n_turns
        if cadence <= 0:
            return
        turns = sum(1 for m in state["messages"] if isinstance(m, HumanMessage))
        if turns == 0 or turns % cadence != 0:
            return
        transcript = "\n".join(
            f"{'Them' if isinstance(m, HumanMessage) else 'Eve'}: {m.content}"
            for m in state["messages"]
            if isinstance(m, HumanMessage | AIMessage)
        )
        summary = await get_model(Tier.REFLEX).ainvoke(
            [
                HumanMessage(
                    "Summarise this conversation in at most four sentences, "
                    "written so someone joining now would know what is going "
                    "on and what is still open.\n\n" + transcript
                )
            ]
        )
        await upsert_digest(thread_id, str(summary.content))
    except Exception:
        logger.warning("digest refresh failed for thread %s", thread_id, exc_info=True)
