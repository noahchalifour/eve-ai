import httpx
import pytest
import respx

from eve_ambient.ntfy import NtfyNotifier


@pytest.fixture(autouse=True)
def ntfy_settings(monkeypatch):
    monkeypatch.setenv("EVE_AMBIENT_NTFY_BASE_URL", "https://ntfy.test")
    monkeypatch.setenv("EVE_AMBIENT_NTFY_TOPIC", "eve-family")
    monkeypatch.setenv("EVE_AMBIENT_NTFY_TOKEN", "tk_secret")
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@respx.mock
async def test_the_message_is_posted_to_the_topic():
    route = respx.post("https://ntfy.test/eve-family").mock(
        return_value=httpx.Response(200)
    )
    sent = await NtfyNotifier().send(
        title="Eve", body="Your dentist appointment is at 3.", urgent=False, click_url=None
    )
    assert sent is True
    assert route.calls.last.request.content.decode() == "Your dentist appointment is at 3."


@respx.mock
async def test_an_urgent_message_raises_the_priority_and_the_tag():
    route = respx.post("https://ntfy.test/eve-family").mock(
        return_value=httpx.Response(200)
    )
    # "Eve - urgent" (ASCII hyphen) is the production-shaped title for an
    # urgent push (eve_ambient.notify.deliver). An em dash here would come
    # back mangled as "Eve ? urgent" by _ascii's errors="replace" -- on
    # every single urgent push, the highest-stakes notification the system
    # sends -- which a title-blind assertion could never catch.
    await NtfyNotifier().send(
        title="Eve - urgent", body="Water detected.", urgent=True, click_url=None
    )
    headers = route.calls.last.request.headers
    assert headers["title"] == "Eve - urgent"
    assert headers["priority"] == "urgent"
    assert "rotating_light" in headers["tags"]


@respx.mock
async def test_a_normal_message_uses_default_priority():
    route = respx.post("https://ntfy.test/eve-family").mock(
        return_value=httpx.Response(200)
    )
    await NtfyNotifier().send(title="Eve", body="Trash day.", urgent=False, click_url=None)
    assert route.calls.last.request.headers["priority"] == "default"


@respx.mock
async def test_the_click_url_is_forwarded():
    route = respx.post("https://ntfy.test/eve-family").mock(
        return_value=httpx.Response(200)
    )
    await NtfyNotifier().send(
        title="Eve", body="hi", urgent=False, click_url="https://eve.test/t/abc"
    )
    assert route.calls.last.request.headers["click"] == "https://eve.test/t/abc"


@respx.mock
async def test_a_non_ascii_click_url_does_not_break_the_request():
    """click_url is built from EVE_AMBIENT_THREAD_URL_TEMPLATE, a
    configuration value -- the likeliest of the header-bound fields to carry
    a non-ASCII character, unlike Tags, whose two possible values are always
    ASCII literals."""
    route = respx.post("https://ntfy.test/eve-family").mock(
        return_value=httpx.Response(200)
    )
    assert await NtfyNotifier().send(
        title="Eve", body="hi", urgent=False, click_url="https://eve.test/t/café"
    ) is True
    assert route.calls.last.request.headers["click"] == "https://eve.test/t/caf?"


@respx.mock
async def test_the_token_is_sent_as_a_bearer():
    route = respx.post("https://ntfy.test/eve-family").mock(
        return_value=httpx.Response(200)
    )
    await NtfyNotifier().send(title="Eve", body="hi", urgent=False, click_url=None)
    assert route.calls.last.request.headers["authorization"] == "Bearer tk_secret"


@respx.mock
async def test_a_failing_push_returns_false_rather_than_raising():
    """ntfy being down must lose the push, not the turn that produced it."""
    respx.post("https://ntfy.test/eve-family").mock(
        side_effect=httpx.ConnectError("refused")
    )
    assert await NtfyNotifier().send(title="Eve", body="hi", urgent=False, click_url=None) is False


@respx.mock
async def test_an_http_error_status_returns_false():
    respx.post("https://ntfy.test/eve-family").mock(return_value=httpx.Response(502))
    assert await NtfyNotifier().send(title="Eve", body="hi", urgent=False, click_url=None) is False


async def test_an_unconfigured_notifier_reports_failure_without_a_request(monkeypatch):
    monkeypatch.delenv("EVE_AMBIENT_NTFY_BASE_URL", raising=False)
    from eve.settings import get_settings

    get_settings.cache_clear()
    assert await NtfyNotifier().send(title="Eve", body="hi", urgent=False, click_url=None) is False


@respx.mock
async def test_a_non_ascii_title_does_not_break_the_request():
    """ntfy carries metadata in headers, which are latin-1 on the wire. Eve's
    body may be any text; the title must not be able to fail the push."""
    route = respx.post("https://ntfy.test/eve-family").mock(
        return_value=httpx.Response(200)
    )
    assert await NtfyNotifier().send(
        title="Eve — urgent", body="Wasserschaden 💧", urgent=True, click_url=None
    ) is True
    assert route.calls.last.request.content.decode() == "Wasserschaden 💧"
