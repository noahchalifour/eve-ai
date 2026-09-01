"""The polled-source registry. `home` is absent deliberately: it is pushed,
not polled (design section 4.4)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from eve_ambient.sources import calendar, computer, finances, mail
from eve_ambient.types import Signal


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    # True: polled once per member holding `permission`, with their sub.
    # False: polled once for the household, with an empty sub.
    per_member: bool
    permission: str
    poll: Callable[[str], Awaitable[list[Signal]]]


SOURCES: tuple[Source, ...] = (
    Source("calendar", True, "calendar.read", calendar.poll),
    Source("mail", True, "mail.read", mail.poll),
    Source("finances", False, "finances", finances.poll),
    Source("computer", False, "computer.use", computer.poll),
)
