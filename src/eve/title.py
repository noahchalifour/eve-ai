"""Best-effort, provider-authored names for a chat's first exchange."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from functools import lru_cache
from typing import Awaitable

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.constants import TAG_NOSTREAM
from pydantic import BaseModel, Field

from eve.memory.db import get_pool
from eve.models import Tier, get_model
from eve.state import is_ambient_text, text_of

logger = logging.getLogger(__name__)

MAX_CHARS = 80
_TIMEOUT_SECONDS = 1.5


class Title(BaseModel):
    title: str = Field(description="A concise, neutral title for this chat.")


@lru_cache(maxsize=1)
def load_title_prompt() -> str:
    from eve.settings import get_settings

    return (get_settings().prompt_file.parent / "title.md").read_text()


def clean(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    title = re.sub(r"\s+", " ", raw).strip().strip('"')
    if not title or len(title) > MAX_CHARS:
        return None
    return title


async def _get_thread(thread_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT metadata_json FROM thread WHERE thread_id = %s", (thread_id,)
        )
        row = await cur.fetchone()
    return {"metadata": row[0]} if row else None


async def _update_thread(thread_id: str, title: str) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE thread SET metadata_json = jsonb_set("
            "metadata_json, '{thread_name}', to_jsonb(%s::text), true), "
            "updated_at = now() WHERE thread_id = %s",
            (title, thread_id),
        )


def _aegra_default_title(human: str) -> str:
    if len(human) <= 100:
        return human
    return human[:100].rsplit(" ", 1)[0] + "..."


def _first_exchange(messages: list) -> tuple[str, str] | None:
    """`text_of` on both sides, not `str(.content)`: a Responses-API AI
    message's content is a list of blocks too, and Phase 3 gives a
    HumanMessage the same `[{"type": "text", ...}, {"type": "eve_image",
    ...}]` shape a raw `str()` would render as a list repr instead of the
    words said."""
    human = next((m for m in messages if isinstance(m, HumanMessage)), None)
    if human is None or is_ambient_text(text_of(human.content)):
        return None
    ai = next((m for m in messages if isinstance(m, AIMessage)), None)
    if ai is None:
        return None
    human_text = text_of(human.content).strip()
    ai_text = text_of(ai.content).strip()
    return (human_text, ai_text) if human_text and ai_text else None


async def title(state: dict, config: RunnableConfig) -> dict:
    """Persist title metadata after the reply has streamed."""
    await generate(state, config, get_thread=_get_thread, update_thread=_update_thread)
    return {}


async def generate(
    state: dict,
    config: RunnableConfig,
    *,
    get_thread: Callable[[str], Awaitable[dict | None]],
    update_thread: Callable[[str, str], Awaitable[None]],
) -> None:
    """Name an untitled, member-authored chat without affecting its turn."""
    thread_id = config.get("configurable", {}).get("thread_id")
    exchange = _first_exchange(state.get("messages") or [])
    if not thread_id or exchange is None:
        return
    try:
        thread = await get_thread(thread_id)
        metadata = thread.get("metadata") if isinstance(thread, dict) else None
        human, ai = exchange
        existing = clean(metadata.get("thread_name")) if isinstance(metadata, dict) else None
        # Aegra pre-fills new threads from the first user message. That is a
        # fallback, not a provider-authored title, so replace only that exact
        # default; preserve a title a provider or member already chose.
        if existing and existing != _aegra_default_title(human):
            return
        model = get_model(Tier.REFLEX).with_structured_output(Title)
        async with asyncio.timeout(_TIMEOUT_SECONDS):
            result = await model.with_config(tags=[TAG_NOSTREAM]).ainvoke(
                [HumanMessage(f"{load_title_prompt()}\n\nMember: {human}\nEve: {ai}")]
            )
        title = clean(getattr(result, "title", None))
        if title:
            await update_thread(thread_id, title)
    except Exception:
        logger.debug("session title generation failed for %s", thread_id, exc_info=True)
