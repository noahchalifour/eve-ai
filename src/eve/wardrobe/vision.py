"""One REFLEX-tier structured-output call per photograph.

REFLEX rather than MECHANICAL because `models.py` says so in as many words:
that tier rides the metered Google key specifically so high-volume grinding
does not consume the ChatGPT subscription limits Noah uses for his own work.
Cataloguing a hundred-garment wardrobe is exactly that shape of work.

The third use of a pattern `memory/extract.py` and `suggest.py` already
establish: `get_model(tier).with_structured_output(Model)`.

This module is where pixels stop. `describe` takes base64 in and returns
garment records out; nothing downstream of it ever sees an image, which is
what keeps `build_specialist` unmodified and Aegra's checkpoints small.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from eve.models import Tier, get_model

logger = logging.getLogger(__name__)

CATEGORIES = ("top", "bottom", "outerwear", "footwear", "accessory", "full")

_PROMPT_FILE = Path("prompts/wardrobe.md")


class WardrobeItem(BaseModel):
    """One garment. Field descriptions are the model's instructions, so they
    are written for it rather than for a reader of this file - the prompt
    carries the longer version."""

    name: str = Field(description="Short spoken-aloud label, e.g. 'navy wool blazer'.")
    category: str = Field(description="One of: top, bottom, outerwear, footwear, accessory, full.")
    colour: str = Field(default="", description="Dominant colour in plain words.")
    pattern: str = Field(default="", description="plain, striped, checked, floral, printed.")
    fabric: str = Field(default="", description="cotton, wool, linen, denim, leather, knit, synthetic, uncertain.")
    warmth: int = Field(default=3, ge=1, le=5, description="1 summer tee, 5 winter parka.")
    formality: int = Field(default=3, ge=1, le=5, description="1 loungewear, 5 black tie.")
    season: str = Field(default="all", description="summer, autumn/winter, all.")
    notes: str = Field(default="", description="One short clause about fit or detail.")


class WardrobeItems(BaseModel):
    """Takes a wrapping object, not a bare list, for the reason
    `suggest.Suggestions` documents: `with_structured_output` wants a schema
    with named fields, and a top-level array is not reliably one."""

    items: list[WardrobeItem] = Field(default_factory=list)


@lru_cache(maxsize=1)
def _prompt() -> str:
    return _PROMPT_FILE.read_text()


def _coerce_category(category: str) -> str:
    """The model is told the six categories and mostly obeys. `accessory` is
    the safe landing place for the rest: a miscatalogued scarf is a nuisance,
    a row with a category nothing groups by is invisible in the rendered
    wardrobe."""
    lowered = (category or "").strip().lower()
    if lowered in CATEGORIES:
        return lowered
    logger.info("coercing unknown wardrobe category %r to 'accessory'", category)
    return "accessory"


async def describe(image_base64: str, content_type: str) -> list[WardrobeItem]:
    """Every garment visible in one photograph. An empty list is a valid and
    expected answer - the prompt tells the model to prefer it over guessing."""
    # ponytail: one vision call per photograph; add retries or batching if
    # transient failures or throughput become a requirement.
    model = get_model(Tier.REFLEX).with_structured_output(WardrobeItems)
    message = HumanMessage(
        content=[
            {"type": "text", "text": _prompt()},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{content_type};base64,{image_base64}"},
            },
        ]
    )
    result = await model.ainvoke([message])
    for item in result.items:
        item.category = _coerce_category(item.category)
    return list(result.items)


def to_row(item: WardrobeItem) -> dict:
    """Split the two stable columns from the jsonb blob. The split lives here,
    beside the schema it splits, so a new field added above lands in `attrs`
    without anyone touching `store.py`."""
    attrs = item.model_dump(exclude={"name", "category"})
    return {"name": item.name, "category": item.category, "attrs": attrs}
