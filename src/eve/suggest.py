"""Reply suggestions: 2-4 things the MEMBER might say next.

One REFLEX-tier structured-output call after Eve's answer has streamed. Not
part of Eve's own turn - see ADR 0013 and the design doc section 2 for why
folding chips into the VOICE call was rejected.

Every failure degrades to no chips. A member must never lose a reply, and a
turn must never hang, because chip generation had a bad day.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import BaseModel, Field

from eve.settings import get_settings

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 4
# Rendered verbatim in a pill. Anything longer is a paragraph, and truncating
# mid-word would put words in the member's mouth.
MAX_CHARS = 80


class Suggestions(BaseModel):
    """The REFLEX model's structured output.

    `default_factory` matters: the prompt licenses an empty list for a
    finished conversation, and a required field would make that answer a
    validation failure indistinguishable from a broken response.
    """

    suggestions: list[str] = Field(
        default_factory=list,
        description="2-4 short first-person things the member might say next.",
    )


def clean(raw: object) -> list[str]:
    """Validate hard: chips are rendered verbatim by a client.

    Takes `object`, not `list[str]`, on purpose. `with_structured_output` is
    contracted to return a `Suggestions`, but a provider or langchain change
    that returns a bare dict or a string must produce no chips rather than an
    AttributeError escaping into the graph.
    """
    if not isinstance(raw, list):
        return []
    kept: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or len(text) > MAX_CHARS:
            continue
        kept.append(text)
        if len(kept) == MAX_SUGGESTIONS:
            break
    return kept


@lru_cache(maxsize=1)
def load_suggest_prompt() -> str:
    return (get_settings().prompt_file.parent / "suggest.md").read_text()
