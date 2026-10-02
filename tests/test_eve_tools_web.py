"""eve_tools.web: SearXNG search and the guarded page fetch (ENG-372).

The fetch runs inside the pod that holds the family's credentials, so most of
these tests are about what it refuses and what it never sends.
"""
from __future__ import annotations

import socket

import httpx
import pytest
import respx

from eve_tools import web


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    from eve_tools.settings import get_tools_settings

    monkeypatch.setenv("EVE_TOOLS_SEARXNG_URL", "http://searxng.test:8080")
    get_tools_settings.cache_clear()
    yield
    get_tools_settings.cache_clear()


def _resolve_to(monkeypatch, mapping: dict[str, list[str]]):
    async def fake_getaddrinfo(host, port, **_kw):
        if host not in mapping:
            raise socket.gaierror("unknown")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port)) for addr in mapping[host]]

    class _Loop:
        getaddrinfo = staticmethod(fake_getaddrinfo)

    monkeypatch.setattr(web.asyncio, "get_running_loop", lambda: _Loop())


# --- search -----------------------------------------------------------


@respx.mock
async def test_search_normalises_and_caps_searxng_results():
    route = respx.get("http://searxng.test:8080/search").mock(return_value=httpx.Response(200, json={
        "results": [
            {"title": f"T{i}", "url": f"https://e.com/{i}", "content": "x" * 1000, "engine": "ddg"}
            for i in range(20)
        ] + [{"title": "no url"}],
    }))
    out = await web.search("langgraph release", max_results=50)
    assert route.called
    assert route.calls.last.request.url.params["format"] == "json"
    assert len(out["results"]) == web.MAX_RESULTS
    assert len(out["results"][0]["snippet"]) == web.MAX_SNIPPET_CHARS
    assert out["results"][0] == {
        "title": "T0", "url": "https://e.com/0", "snippet": "x" * 300, "engine": "ddg",
    }


async def test_search_unconfigured_fails_loudly(monkeypatch):
    from eve_tools.settings import get_tools_settings

    monkeypatch.delenv("EVE_TOOLS_SEARXNG_URL")
    get_tools_settings.cache_clear()
    with pytest.raises(RuntimeError, match="not configured"):
        await web.search("anything")


async def test_search_rejects_an_empty_query():
    with pytest.raises(ValueError):
        await web.search("   ")


# --- fetch: refusals --------------------------------------------------


@pytest.mark.parametrize("url", [
    "ftp://example.com/file",
    "file:///etc/passwd",
    "http://user:pw@example.com/",
    "http://localhost/",
    "http://127.0.0.1/",
    "http://10.0.0.1/",
    "http://192.168.1.10/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[::ffff:10.0.0.1]/",
    "http://eve-tools.eve.svc/",
    "http://postgres.eve.svc.cluster.local/",
    "http://homeassistant.local/",
    "http://intranet/",
])
async def test_fetch_refuses_non_public_targets(url):
    with pytest.raises(web.FetchRefused):
        await web.fetch(url)


async def test_fetch_refuses_a_name_resolving_to_a_private_address(monkeypatch):
    _resolve_to(monkeypatch, {"evil.example": ["93.184.216.34", "10.0.0.5"]})
    with pytest.raises(web.FetchRefused, match="non-public"):
        await web.fetch("https://evil.example/")


@respx.mock
async def test_fetch_refuses_a_redirect_into_the_cluster(monkeypatch):
    _resolve_to(monkeypatch, {"example.com": ["93.184.216.34"]})
    respx.get("https://93.184.216.34/start").mock(
        return_value=httpx.Response(302, headers={"location": "http://10.1.2.3/secret"})
    )
    with pytest.raises(web.FetchRefused, match="not a public address"):
        await web.fetch("https://example.com/start")


@respx.mock
async def test_fetch_refuses_unreadable_content_types(monkeypatch):
    _resolve_to(monkeypatch, {"example.com": ["93.184.216.34"]})
    respx.get("https://93.184.216.34/a.zip").mock(
        return_value=httpx.Response(200, headers={"content-type": "application/zip"}, content=b"PK")
    )
    with pytest.raises(web.FetchRefused, match="application/zip"):
        await web.fetch("https://example.com/a.zip")


@respx.mock
async def test_fetch_refuses_oversized_bodies(monkeypatch):
    _resolve_to(monkeypatch, {"example.com": ["93.184.216.34"]})
    respx.get("https://93.184.216.34/big").mock(return_value=httpx.Response(
        200, headers={"content-type": "text/plain"}, content=b"a" * (web.MAX_DOWNLOAD_BYTES + 1)
    ))
    with pytest.raises(web.FetchRefused, match="5 MB"):
        await web.fetch("https://example.com/big")


@respx.mock
async def test_fetch_gives_up_after_too_many_redirects(monkeypatch):
    _resolve_to(monkeypatch, {"example.com": ["93.184.216.34"]})
    respx.get(url__regex=r"https://93\.184\.216\.34/r\d+").mock(
        side_effect=lambda request: httpx.Response(
            302, headers={"location": f"/r{int(request.url.path[2:]) + 1}"}
        )
    )
    with pytest.raises(web.FetchRefused, match="redirects"):
        await web.fetch("https://example.com/r0")


# --- fetch: success and what is sent ----------------------------------


@respx.mock
async def test_fetch_connects_to_the_checked_address_and_sends_no_credentials(monkeypatch):
    _resolve_to(monkeypatch, {"example.com": ["93.184.216.34"]})
    html = (
        "<html><head><title>Hello &amp; welcome</title><script>steal()</script></head>"
        "<body><nav>menu</nav><h1>Heading</h1><p>First   para.</p><p>Second</p></body></html>"
    )
    route = respx.get("https://93.184.216.34/page").mock(return_value=httpx.Response(
        200, headers={"content-type": "text/html; charset=utf-8", "set-cookie": "s=1"}, text=html
    ))
    out = await web.fetch("https://example.com/page")

    request = route.calls.last.request
    # Pinned: the request went to the address the guard approved, with the
    # original name kept for Host and SNI.
    assert request.url.host == "93.184.216.34"
    assert request.headers["host"] == "example.com"
    assert request.extensions.get("sni_hostname") == "example.com"
    # Nothing that could carry a credential.
    assert "authorization" not in request.headers
    assert "cookie" not in request.headers
    assert request.headers["user-agent"] == web.USER_AGENT

    assert out["title"] == "Hello & welcome"
    assert "steal" not in out["text"] and "menu" not in out["text"]
    assert out["text"] == "Heading\nFirst para.\nSecond"
    assert out["truncated"] is False
    assert out["final_url"] == "https://example.com/page"


@respx.mock
async def test_fetch_follows_a_public_redirect_and_truncates(monkeypatch):
    _resolve_to(monkeypatch, {"example.com": ["93.184.216.34"], "www.example.com": ["93.184.216.35"]})
    respx.get("https://93.184.216.34/old").mock(
        return_value=httpx.Response(301, headers={"location": "https://www.example.com/new"})
    )
    respx.get("https://93.184.216.35/new").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/plain"}, text="z" * 3000)
    )
    out = await web.fetch("https://example.com/old", max_chars=1000)
    assert out["final_url"] == "https://www.example.com/new"
    assert out["text"] == "z" * 1000
    assert out["truncated"] is True


async def test_fetch_never_reuses_a_credentialed_client(monkeypatch):
    """Every AsyncClient the fetch builds is constructed here, with no auth,
    no cookies and no default headers beyond User-Agent/Accept."""
    _resolve_to(monkeypatch, {"example.com": ["93.184.216.34"]})
    built: list[dict] = []
    real = httpx.AsyncClient

    def spy(*args, **kwargs):
        built.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(web.httpx, "AsyncClient", spy)
    with respx.mock:
        respx.get("https://93.184.216.34/").mock(
            return_value=httpx.Response(200, headers={"content-type": "text/plain"}, text="ok")
        )
        await web.fetch("https://example.com/")
    assert built
    for kwargs in built:
        assert "auth" not in kwargs and "cookies" not in kwargs
        assert set(kwargs["headers"]) <= {"User-Agent", "Accept"}


def test_html_to_text_drops_chrome_and_comments():
    title, text = web.html_to_text(
        "<title>T</title><style>.a{}</style><!-- hidden --><article><p>One</p><br>Two</article>"
    )
    assert title == "T"
    assert text == "One\nTwo"
