"""Post-stream memory extraction and durable-memory writes."""

from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.constants import TAG_NOSTREAM
from opentelemetry import trace

from eve.memory import pending
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
from eve.state import is_ambient_text, may_author
from eve.ui import protocol

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("eve.memory.extract")

# Bound at module level so tests can monkeypatch it, and lazily resolved at
# call time so the import direction stays eve_ambient -> eve.
mark_replied = None  # set on first use by _mark_replied_if_a_reply

_CAPPED = {
    "profile": "memory_profile_cap",
    "household": "memory_household_cap",
    # Phase 5a: without a cap, a year of small corrections becomes a prompt
    # preamble longer than the conversation (design doc section 5.1).
    "rule": "memory_rule_cap",
}


@lru_cache(maxsize=1)
def load_extract_prompt() -> str:
    return (get_settings().prompt_file.parent / "extract.md").read_text()


def _resolve_scope(op: Operation, member: dict) -> tuple[str, str, str]:
    """Resolve layer and scope after enforcing shared-write permission.

    A rule is member-scoped unless the model asked for a household one, which
    needs the same memory.write_shared permission a household fact needs. One
    code path for both, so a kid cannot author a rule that changes how Eve
    treats the whole family (design doc section 6.4).
    """
    shared = op.layer == "household" or (op.layer == "rule" and op.shared)
    if shared:
        if "memory.write_shared" in (member.get("permissions") or []):
            return ("rule" if op.layer == "rule" else "household"), "household", ""
        return ("rule" if op.layer == "rule" else "profile"), "member", member["sub"]
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


def _visible_content(message: HumanMessage | AIMessage) -> object:
    """What REFLEX and the digest summarizer are allowed to read as
    something a party to the conversation said. `persist_ui`
    (`eve.ui.persist`) writes this turn's `<assistant-ui>` frame into the
    final AIMessage so a reopened session still has the card - not so a
    later model can read it back. Left in, REFLEX can mint junk memories
    out of the surface JSON ("temperature 20", "condition sunny") as if Eve
    had said them, and a digest refresh would summarise a card as prose. A
    `HumanMessage` never carries a frame, so it passes through untouched."""
    if isinstance(message, AIMessage):
        return protocol.strip_frames_from_content(message.content)
    return message.content


def _last_exchange(messages: list) -> tuple[str, str]:
    human = next(
        (m.content for m in reversed(messages) if isinstance(m, HumanMessage)), ""
    )
    ai = next(
        (_visible_content(m) for m in reversed(messages) if isinstance(m, AIMessage)), ""
    )
    return str(human), str(ai)


_AUTHORED_LAYERS = ("rule", "procedure")


def _filter_authored(
    ops: list[Operation], human: str, rule_ids: set[str]
) -> tuple[list[Operation], int]:
    """Drop rule and procedure operations unless this turn may author.

    Fails CLOSED on the ambiguous case: a turn that cannot be attributed to a
    member speaking authors nothing. `procedure` is dropped unconditionally -
    procedures come from write_skill, never from a REFLEX pass (design doc
    sections 4.2 and 6.2).

    Authoring a new rule is not the only way an untrusted turn could change
    standing behaviour: `supersede`/`forget` can erase an existing one just as
    well, and the candidate list handed to the model includes rule-layer ids
    to judge overlap against. So when this turn may not author, any
    `supersede`/`forget` naming a rule id from `rule_ids` is dropped too - a
    turn that cannot add a rule cannot delete or replace one either. This does
    not touch `supersede`/`forget` of non-rule facts: fact extraction on an
    ambient turn is unchanged.

    The predicate itself lives in eve.state, shared with write_skill: one
    guard, two authoring paths.
    """
    allowed = may_author(human)
    kept, rejected = [], 0
    for op in ops:
        layer = getattr(op, "layer", None)
        targets_rule = op.op in ("supersede", "forget") and op.target_id in rule_ids
        if (
            layer == "procedure"
            or (layer in _AUTHORED_LAYERS and not allowed)
            or (not allowed and targets_rule)
        ):
            rejected += 1
            continue
        kept.append(op)
    return kept, rejected


async def extract(state: dict, config: RunnableConfig) -> dict:
    """End the turn, then do the bookkeeping.

    The node returns `{}` immediately and the real work runs in the
    background, because the run is only complete when the graph reaches END -
    an in-graph extraction holds the SSE stream, and so the client's "done",
    open for a REFLEX call plus embeddings plus writes. `recall` joins this
    task on the next turn before it reads memory, so detaching costs no
    ordering (ADR 0012).
    """
    if not get_settings().memory_extract_background:
        await _run_extraction(state, config)
        return {}
    thread_id = config.get("configurable", {}).get("thread_id")
    pending.spawn(thread_id, _run_extraction(state, config))
    return {}


async def _run_extraction(state: dict, config: RunnableConfig) -> None:
    """Extract memory after Eve's answer without allowing failures to fail a turn.

    Opens its OWN span rather than writing to the ambient one. When this runs
    detached, the run's span has already ended, and OpenTelemetry silently
    drops attributes set on an ended span - every `eve.extract.*` and
    `eve.authoring.*` number would read as absent. The task inherits the
    context at creation time, so this span still parents correctly.
    """
    member = state["member"]
    thread_id = config.get("configurable", {}).get("thread_id")
    run_id = config.get("configurable", {}).get("run_id")
    human, ai = _last_exchange(state["messages"])
    if not human:
        return

    with _tracer.start_as_current_span("eve.extract") as span:
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
            result = await model.with_config(tags=[TAG_NOSTREAM]).ainvoke(
                [HumanMessage(prompt)]
            )
            rule_ids = {m.id for m in candidates if getattr(m, "layer", None) == "rule"}
            operations, rejected = _filter_authored(
                list(result.operations), human, rule_ids
            )
            counts = await apply_operations(operations, member, thread_id, run_id)
            rules_written = sum(
                1 for op in operations if getattr(op, "layer", None) == "rule"
            )
        except Exception:
            logger.warning("extraction failed for thread %s", thread_id, exc_info=True)
            span.set_attribute("eve.extract.failed", True)
            return

        for op_name in ("add", "supersede", "reinforce", "forget", "evict"):
            span.set_attribute(f"eve.extract.ops.{op_name}", counts.get(op_name, 0))
        # Design doc section 9: the plausible failure of this phase is that
        # authoring never fires at all. These two numbers are how that is
        # detected, and how a firing guard is distinguished from a silent model.
        span.set_attribute("eve.authoring.rules_written", rules_written)
        span.set_attribute("eve.authoring.rules_rejected", rejected)

    await _maybe_refresh_digest(state, thread_id)
    await _mark_replied_if_a_reply(human, thread_id)


async def _mark_replied_if_a_reply(human: str, thread_id: str | None) -> None:
    """Stamp the ambient reply label. Deliberately lazy-imported: eve_ambient
    depends on eve, never the reverse, and a module-level import here would
    invert that."""
    if thread_id is None or is_ambient_text(human):
        return
    fn = mark_replied
    if fn is None:
        from eve_ambient.store import mark_replied as fn  # noqa: PLC0415
    try:
        await fn(thread_id)
    except Exception:
        logger.debug("could not stamp the ambient reply label", exc_info=True)


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
            f"{'Them' if isinstance(m, HumanMessage) else 'Eve'}: {_visible_content(m)}"
            for m in state["messages"]
            if isinstance(m, HumanMessage | AIMessage)
        )
        summary = await get_model(Tier.REFLEX).with_config(tags=[TAG_NOSTREAM]).ainvoke(
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
