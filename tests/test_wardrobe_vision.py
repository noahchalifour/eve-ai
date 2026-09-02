"""tests/test_wardrobe_vision.py"""
from unittest.mock import AsyncMock, MagicMock

from eve.wardrobe import vision


def _model_returning(items):
    """A stand-in for `get_model(...).with_structured_output(WardrobeItems)`."""
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=vision.WardrobeItems(items=items))
    model = MagicMock()
    model.with_structured_output = MagicMock(return_value=structured)
    return model, structured


async def test_describe_returns_the_models_items(monkeypatch):
    item = vision.WardrobeItem(
        name="navy wool blazer",
        category="outerwear",
        colour="navy",
        pattern="plain",
        fabric="wool",
        warmth=4,
        formality=4,
        season="autumn/winter",
        notes="single-breasted, notch lapel",
    )
    model, _ = _model_returning([item])
    monkeypatch.setattr(vision, "get_model", lambda _tier: model)

    result = await vision.describe("aGVsbG8=", "image/jpeg")

    assert [i.name for i in result] == ["navy wool blazer"]


async def test_describe_sends_the_image_as_a_data_url(monkeypatch):
    model, structured = _model_returning([])
    monkeypatch.setattr(vision, "get_model", lambda _tier: model)

    await vision.describe("aGVsbG8=", "image/png")

    messages = structured.ainvoke.await_args.args[0]
    blocks = messages[-1].content
    image_block = next(b for b in blocks if b["type"] == "image_url")
    assert image_block["image_url"]["url"] == "data:image/png;base64,aGVsbG8="
    text_block = next(b for b in blocks if b["type"] == "text")
    assert "garment" in text_block["text"].lower()


async def test_describe_runs_on_the_reflex_tier(monkeypatch):
    from eve.models import Tier

    seen = []
    model, _ = _model_returning([])

    def _get_model(tier):
        seen.append(tier)
        return model

    monkeypatch.setattr(vision, "get_model", _get_model)

    await vision.describe("aGVsbG8=", "image/jpeg")

    assert seen == [Tier.REFLEX]


async def test_an_unknown_category_is_coerced_to_accessory(monkeypatch):
    item = vision.WardrobeItem(
        name="thing",
        category="spacesuit",
        colour="silver",
        pattern="plain",
        fabric="nylon",
        warmth=3,
        formality=1,
        season="all",
        notes="",
    )
    model, _ = _model_returning([item])
    monkeypatch.setattr(vision, "get_model", lambda _tier: model)

    result = await vision.describe("aGVsbG8=", "image/jpeg")

    assert result[0].category == "accessory"


def test_to_row_splits_the_two_stable_columns_from_attrs():
    item = vision.WardrobeItem(
        name="brown chelsea boots",
        category="footwear",
        colour="brown",
        pattern="plain",
        fabric="leather",
        warmth=3,
        formality=3,
        season="all",
        notes="elastic gusset",
    )

    row = vision.to_row(item)

    assert row["name"] == "brown chelsea boots"
    assert row["category"] == "footwear"
    assert row["attrs"] == {
        "colour": "brown",
        "pattern": "plain",
        "fabric": "leather",
        "warmth": 3,
        "formality": 3,
        "season": "all",
        "notes": "elastic gusset",
    }
