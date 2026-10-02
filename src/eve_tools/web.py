"""Web search and page reading (ENG-372).

Both live in eve-tools rather than Eve's own pod (ADR 0022): Eve's container
keeps its egress restricted to eve-tools, LiteLLM and Postgres, and the web
becomes one more upstream eve-tools reaches.

This pod also holds the family's credentials, which is why `fetch` is built
the way it is:

* Its own `httpx.AsyncClient`, created per call with a fixed User-Agent and
  nothing else. No cookie jar survives a call, no auth header is ever set,
  and no other module's client (Home Assistant, Gmail, Monarch...) is reused,
  so a fetched URL cannot be handed a token by accident.
* An SSRF guard that resolves the host itself and refuses private, loopback,
  link-local, multicast, reserved and cluster addresses, and then CONNECTS TO
  THE ADDRESS IT CHECKED (the Host header and SNI still carry the name). A
  second DNS lookup by the HTTP library could otherwise answer differently
  (DNS rebinding). Redirects are followed by hand so every hop is checked
  the same way.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import logging
import re
import socket
from html import unescape
from urllib.parse import urljoin, urlsplit

import httpx

from eve_tools.settings import get_tools_settings

logger = logging.getLogger(__name__)

USER_AGENT = "EveAssistant/1.0 (+family assistant; read-only page fetch)"

MAX_RESULTS = 10
MAX_SNIPPET_CHARS = 300
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 10.0
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_CHARS_CEILING = 20000

_ALLOWED_TYPES = ("text/html", "text/plain", "application/json", "application/pdf",
                  "application/xhtml+xml")

# Names that only make sense inside the cluster or the lab, refused before
# any lookup. The address check below is the real control; this keeps the
# refusal message legible for the common case.
_INTERNAL_SUFFIXES = (".svc", ".cluster.local", ".local", ".internal", ".lan", ".home.arpa")


class FetchRefused(Exception):
    """The URL is not one eve-tools will fetch. The message goes to the model."""


# --- search -----------------------------------------------------------


async def search(query: str, max_results: int = 5) -> dict:
    settings = get_tools_settings()
    if not settings.searxng_url:
        raise RuntimeError("web search is not configured (EVE_TOOLS_SEARXNG_URL)")
    query = (query or "").strip()
    if not query:
        raise ValueError("query must not be empty")
    limit = max(1, min(int(max_results or 5), MAX_RESULTS))
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(
            f"{settings.searxng_url.rstrip('/')}/search",
            params={"q": query, "format": "json", "safesearch": 1},
        )
        response.raise_for_status()
        body = response.json()
    results = []
    for item in body.get("results") or []:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        entry = {
            "title": str(item.get("title") or "")[:200],
            "url": str(item["url"]),
            "snippet": str(item.get("content") or "")[:MAX_SNIPPET_CHARS],
            "engine": str(item.get("engine") or ""),
        }
        if item.get("publishedDate"):
            entry["published_date"] = str(item["publishedDate"])
        results.append(entry)
        if len(results) >= limit:
            break
    return {"query": query, "results": results}


# --- fetch ------------------------------------------------------------


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def _resolve_public(host: str, port: int) -> str:
    """One public address for `host`, or FetchRefused. Every address the name
    resolves to must be public: a name with one public and one private record
    is exactly what a rebinding attack looks like."""
    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(_INTERNAL_SUFFIXES) or "." not in lowered and not _looks_like_ip(lowered):
        raise FetchRefused(f"{host} is an internal name")
    if _looks_like_ip(lowered):
        if not _is_public(lowered):
            raise FetchRefused(f"{host} is not a public address")
        return lowered
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(lowered, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchRefused(f"could not resolve {host}") from exc
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise FetchRefused(f"could not resolve {host}")
    for address in addresses:
        if not _is_public(address):
            raise FetchRefused(f"{host} resolves to a non-public address")
    return addresses[0]


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def _check_url(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchRefused("only http and https URLs can be read")
    if parts.username or parts.password:
        raise FetchRefused("URLs carrying credentials are refused")
    host = (parts.hostname or "").strip("[]")
    if not host:
        raise FetchRefused("the URL has no host")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme, host, port


class _PinnedTransport(httpx.AsyncBaseTransport):
    """Sends each request to the address the guard already approved, keeping
    the hostname for the Host header and TLS (SNI + certificate check)."""

    def __init__(self, address: str) -> None:
        self._address = address
        self._inner = httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        target = self._address if ":" not in self._address else f"[{self._address}]"
        request.extensions = {**request.extensions, "sni_hostname": host}
        request.headers["Host"] = request.url.netloc.decode("ascii")
        request.url = request.url.copy_with(host=target.strip("[]"))
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def _content_type(response: httpx.Response) -> str:
    return (response.headers.get("content-type") or "").split(";")[0].strip().lower()


async def _download(url: str) -> tuple[str, httpx.Headers, bytes, str]:
    """GET `url` with every hop guarded. Returns (final_url, headers, body, type)."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        _scheme, host, port = _check_url(current)
        address = await _resolve_public(host, port)
        transport = _PinnedTransport(address)
        # Fresh client per hop: no cookie jar, no auth, nothing shared.
        async with httpx.AsyncClient(
            transport=transport,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,application/json,application/pdf;q=0.9,*/*;q=0.1"},
        ) as client:
            async with client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchRefused("redirect without a location")
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise FetchRefused(f"the page answered HTTP {response.status_code}")
                kind = _content_type(response)
                if kind and kind not in _ALLOWED_TYPES:
                    raise FetchRefused(f"{kind} content is not something I can read")
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > MAX_DOWNLOAD_BYTES:
                    raise FetchRefused("the page is larger than 5 MB")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_DOWNLOAD_BYTES:
                        raise FetchRefused("the page is larger than 5 MB")
                return current, response.headers, bytes(body), kind or "text/plain"
    raise FetchRefused(f"more than {MAX_REDIRECTS} redirects")


_DROP_BLOCKS = re.compile(
    r"<(head|title|script|style|noscript|svg|nav|footer|header|form|iframe|template|aside)\b.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_BLOCK_TAGS = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr|table|section|article|blockquote|pre)\b[^>]*>", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def html_to_text(html: str) -> tuple[str, str]:
    """(title, readable text). Deliberately simple: drop the chrome blocks,
    turn block tags into newlines, strip the rest, collapse whitespace. Good
    enough for articles and docs; JS-rendered pages belong to eve-computer."""
    title_match = _TITLE.search(html)
    title = unescape(_TAGS.sub("", title_match.group(1))).strip() if title_match else ""
    body = _DROP_BLOCKS.sub(" ", html)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.DOTALL)
    body = _BLOCK_TAGS.sub("\n", body)
    body = unescape(_TAGS.sub(" ", body))
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in body.splitlines()]
    text = "\n".join(line for line in lines if line)
    return title, text


def _pdf_to_text(data: bytes) -> tuple[str, str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    title = ""
    if reader.metadata and reader.metadata.title:
        title = str(reader.metadata.title)
    pages = []
    total = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)
        total += len(text)
        if total > MAX_CHARS_CEILING * 2:
            break
    return title, "\n".join(pages).strip()


async def fetch(url: str, max_chars: int = 8000) -> dict:
    limit = max(500, min(int(max_chars or 8000), MAX_CHARS_CEILING))
    url = (url or "").strip()
    final_url, headers, body, kind = await _download(url)
    if kind == "application/pdf":
        title, text = await asyncio.to_thread(_pdf_to_text, body)
    else:
        charset = "utf-8"
        match = re.search(r"charset=([\w-]+)", headers.get("content-type") or "")
        if match:
            charset = match.group(1)
        try:
            decoded = body.decode(charset, errors="replace")
        except LookupError:
            decoded = body.decode("utf-8", errors="replace")
        if kind in ("text/html", "application/xhtml+xml") or (not kind and "<html" in decoded[:500].lower()):
            title, text = html_to_text(decoded)
        else:
            title, text = "", decoded.strip()
    truncated = len(text) > limit
    return {
        "url": url,
        "final_url": final_url,
        "title": title,
        "text": text[:limit],
        "truncated": truncated,
    }
