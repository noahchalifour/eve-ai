"""Delivery. One protocol, one implementation — swappable as the design asks,
without a factory for a single product.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from eve.settings import get_settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


class Notifier(Protocol):
    async def send(
        self, *, title: str, body: str, urgent: bool, click_url: str | None
    ) -> bool:
        """True when the push was accepted. Never raises: a delivery failure
        must not lose the thread the message is already in."""
        ...


def _ascii(value: str) -> str:
    """ntfy carries title and tags in HTTP headers, which are latin-1 on the
    wire. Eve's own text goes in the body, where UTF-8 is fine; anything
    header-bound is flattened so an em dash can never fail a push."""
    return value.encode("ascii", "replace").decode("ascii")


class NtfyNotifier:
    async def send(
        self, *, title: str, body: str, urgent: bool, click_url: str | None
    ) -> bool:
        settings = get_settings()
        if not settings.ambient_ntfy_base_url or not settings.ambient_ntfy_topic:
            logger.warning("ntfy is not configured; dropping a notification")
            return False

        headers = {
            "Title": _ascii(title),
            "Priority": "urgent" if urgent else "default",
            "Tags": "rotating_light" if urgent else "speech_balloon",
        }
        if settings.ambient_ntfy_token:
            headers["Authorization"] = f"Bearer {settings.ambient_ntfy_token}"
        if click_url:
            headers["Click"] = click_url

        url = f"{settings.ambient_ntfy_base_url.rstrip('/')}/{settings.ambient_ntfy_topic}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(
                    url, content=body.encode("utf-8"), headers=headers
                )
                response.raise_for_status()
        except Exception:
            logger.warning("ntfy push failed", exc_info=True)
            return False
        return True
