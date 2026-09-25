"""References to pixels, at the model boundary and nowhere else (spec 3.2).

`EveState` holds `{"type": "eve_image", "image_id", "alt"}` blocks - a few
dozen bytes each, so Aegra's per-turn checkpoint does not grow with image
size. This module rewrites a COPY of the message list right before a model
call. Nothing it returns is ever written back to state.
"""

from __future__ import annotations

import base64
import logging

from langchain_core.messages import HumanMessage

from eve.images import store
from eve.models import Tier, get_model
from eve.settings import get_settings
from eve.state import text_of

logger = logging.getLogger(__name__)

UNAVAILABLE = "[image no longer available]"
_CAPTION_PROMPT = (
    "Describe this photo in one sentence for someone who cannot see it. "
    "Name colours, garments, objects and any visible text. No preamble."
)


def reference(image_id: str, alt: str) -> dict:
    return {"type": "eve_image", "image_id": image_id, "alt": alt}


def _is_reference(block: object) -> bool:
    return isinstance(block, dict) and block.get("type") == "eve_image"


def has_references(messages: list) -> bool:
    return any(
        isinstance(m, HumanMessage) and isinstance(m.content, list)
        and any(_is_reference(b) for b in m.content)
        for m in messages
    )


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


async def _describe(row: store.ImageRow) -> str:
    message = HumanMessage(content=[
        _text(_CAPTION_PROMPT),
        {"type": "image", "base64": base64.b64encode(row.bytes).decode(),
         "mime_type": row.content_type},
    ])
    reply = await get_model(Tier.REFLEX).ainvoke([message])
    return text_of(reply.content).strip() or "a photo"


async def caption(row: store.ImageRow) -> str:
    """Generated once per image on REFLEX (Gemini, multimodal since EVE-20's
    wardrobe/vision.py) and cached on the row."""
    if row.caption:
        return row.caption
    try:
        text = await _describe(row)
    except Exception:
        logger.warning("captioning image %s failed", row.id, exc_info=True)
        return "a photo"
    await store.set_caption(row.id, row.member_sub, text)
    return text


async def _expand(block: dict, member_sub: str | None, *, native: bool) -> list[dict]:
    row = await store.get(str(block.get("image_id", "")), member_sub) if member_sub else None
    if row is None:
        return [_text(UNAVAILABLE)]
    short = store.short_id(row.id)
    if not native:
        return [_text(f"[image {short}: {await caption(row)}]")]
    return [
        _text(f"[image {short}]"),
        {"type": "image", "base64": base64.b64encode(row.bytes).decode(),
         "mime_type": row.content_type},
    ]


async def hydrate(
    messages: list,
    member_sub: str | None,
    *,
    native: bool,
    window: int | None = None,
) -> list:
    window = get_settings().image_hydrate_turns if window is None else window
    human_positions = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    recent = set(human_positions[-window:]) if window > 0 else set()

    out = []
    for index, message in enumerate(messages):
        content = message.content if isinstance(message, HumanMessage) else None
        if not isinstance(content, list) or not any(_is_reference(b) for b in content):
            out.append(message)
            continue
        blocks: list = []
        for block in content:
            if not _is_reference(block):
                blocks.append(block)
            elif index in recent:
                blocks.extend(await _expand(block, member_sub, native=native))
            else:
                short = store.short_id(str(block.get("image_id", "")))
                blocks.append(_text(f"[image {short}: {block.get('alt') or 'a photo'}]"))
        out.append(message.model_copy(update={"content": blocks}))
    return out
