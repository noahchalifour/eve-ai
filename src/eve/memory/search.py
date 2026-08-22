"""Search past conversations and household memory for something not in
your current context - a decision, an event, a detail from weeks ago.

search_memory: deliberate recall, available as a tool. Unlike the
unconditional `recall` node (memory/recall.py), this has no time budget -
it only ever runs mid-turn, after the first token has already streamed
(design doc section 6, honoring memory design section 13's own commitment).
"""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from eve.memory.embed import embed_query
from eve.memory.ranking import fuse
from eve.memory.store import search_episodic_lexical, search_episodic_vector
from eve.memory.types import Memory
from eve.state import EveState


@tool
async def search_memory(query: str, state: Annotated[EveState, InjectedState]) -> str:
    """Search past conversations and household memory for something not in
    your current context - a decision, an event, a detail from weeks ago."""
    sub = state["member"]["sub"]
    lexical = await search_episodic_lexical(sub, query, limit=10)
    try:
        vector = await search_episodic_vector(sub, await embed_query(query), limit=10)
    except Exception:
        vector = []

    by_id = {m.id: m for m in (*lexical, *vector)}
    order = fuse([m.id for m in lexical], [m.id for m in vector])
    results: list[Memory] = [by_id[i] for i in order][:10]
    if not results:
        return "Nothing found."
    return "\n".join(f"- {m.content}" for m in results)
