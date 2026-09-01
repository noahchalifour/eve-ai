import pytest


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_computer_is_disabled_by_default():
    from eve.settings import Settings

    assert Settings().computer_enabled is False
    assert Settings().computer_base_url == "http://eve-computer:8092"


def test_a_short_computer_api_key_is_rejected():
    from eve.settings import Settings

    with pytest.raises(ValueError, match="EVE_COMPUTER_API_KEY"):
        Settings(computer_api_key="too-short")


def test_enabling_without_a_key_is_rejected():
    from eve.settings import Settings

    with pytest.raises(ValueError, match="EVE_COMPUTER_API_KEY is required"):
        Settings(computer_enabled=True)


def test_enabling_with_a_long_enough_key_is_accepted():
    from eve.settings import Settings

    settings = Settings(computer_enabled=True, computer_api_key="k" * 32)
    assert settings.computer_enabled is True
