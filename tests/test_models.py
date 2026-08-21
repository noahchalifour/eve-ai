from eve.models import TIER_MODELS, Tier, get_model


def test_every_tier_is_mapped_and_reflex_is_not_on_the_chatgpt_proxy():
    """Phase 1 left REFLEX unmapped (`None`), raising NotImplementedError.
    Phase 2 closes that gap: every tier now resolves to a real model string.
    REFLEX specifically must NOT be a `chatgpt/*` model - it runs on the
    metered Gemini key, not the ChatGPT subscription proxy (spec 2.1)."""
    for tier in Tier:
        assert TIER_MODELS[tier]
        if tier is Tier.REFLEX:
            assert not TIER_MODELS[tier].startswith("chatgpt/")
        else:
            assert TIER_MODELS[tier].startswith("chatgpt/")


def test_voice_tier_is_the_chatgpt_conversational_model():
    assert TIER_MODELS[Tier.VOICE] == "chatgpt/gpt-5.6-terra"


def test_model_is_pointed_at_litellm(monkeypatch):
    monkeypatch.setenv("EVE_LITELLM_BASE_URL", "http://litellm.test/v1")
    monkeypatch.setenv("EVE_LITELLM_API_KEY", "sk-test")
    get_model.cache_clear()
    from eve.settings import get_settings

    get_settings.cache_clear()

    model = get_model(Tier.VOICE)
    assert model.model_name == "chatgpt/gpt-5.6-terra"
    assert "litellm.test" in str(model.openai_api_base)


def test_reflex_tier_is_the_metered_gemini_model():
    """REFLEX runs on every turn and must not spend the ChatGPT
    subscription's rate limits (spec 2.1). A regression here is invisible
    until Noah's own Codex sessions start getting throttled."""
    assert TIER_MODELS[Tier.REFLEX] == "gemini/gemini-flash-lite-latest"


def test_only_chatgpt_tiers_use_the_responses_api():
    assert get_model(Tier.VOICE).use_responses_api is True
    assert get_model(Tier.REFLEX).use_responses_api is False


def test_voice_model_declares_streaming(monkeypatch):
    """`streaming=True` must be explicitly set, not merely truthy by default:
    langchain-core's `_should_stream` consults `model_fields_set`, so a model
    that inherited the default would not route `ainvoke` through `_astream`
    and Eve's first token would arrive with her last one."""
    monkeypatch.setenv("EVE_LITELLM_API_KEY", "sk-test")
    model = get_model(Tier.VOICE)
    assert "streaming" in model.model_fields_set
    assert model.streaming is True
