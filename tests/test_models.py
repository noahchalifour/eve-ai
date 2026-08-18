import pytest

from eve.models import TIER_MODELS, Tier, get_model


def test_every_tier_except_reflex_is_mapped():
    for tier in Tier:
        if tier is Tier.REFLEX:
            assert TIER_MODELS[tier] is None
        else:
            assert TIER_MODELS[tier].startswith("chatgpt/")


def test_voice_tier_is_the_chatgpt_conversational_model():
    assert TIER_MODELS[Tier.VOICE] == "chatgpt/gpt-5.3-chat-latest"


def test_model_is_pointed_at_litellm(monkeypatch):
    monkeypatch.setenv("EVE_LITELLM_BASE_URL", "http://litellm.test/v1")
    monkeypatch.setenv("EVE_LITELLM_API_KEY", "sk-test")
    get_model.cache_clear()
    from eve.settings import get_settings

    get_settings.cache_clear()

    model = get_model(Tier.VOICE)
    assert model.model_name == "chatgpt/gpt-5.3-chat-latest"
    assert "litellm.test" in str(model.openai_api_base)


def test_reflex_tier_is_not_available_until_phase_2():
    with pytest.raises(NotImplementedError, match="Phase 2"):
        get_model(Tier.REFLEX)


def test_voice_model_declares_streaming(monkeypatch):
    """`streaming=True` must be explicitly set, not merely truthy by default:
    langchain-core's `_should_stream` consults `model_fields_set`, so a model
    that inherited the default would not route `ainvoke` through `_astream`
    and Eve's first token would arrive with her last one."""
    monkeypatch.setenv("EVE_LITELLM_API_KEY", "sk-test")
    model = get_model(Tier.VOICE)
    assert "streaming" in model.model_fields_set
    assert model.streaming is True
