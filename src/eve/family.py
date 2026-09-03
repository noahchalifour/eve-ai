"""The family roster: who Eve serves and what each member may do.

The roster holds no secrets - names, roles, and capability grants - so it
lives in git rather than Vault. Pull request history is its audit log.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from eve.settings import get_settings


class UnknownMemberError(Exception):
    """Raised when an authenticated subject is not in the roster."""


@dataclass(frozen=True)
class Member:
    sub: str
    name: str
    role: str
    timezone: str
    permissions: frozenset[str]
    # The Immich album holding this member's clothes. Optional: a member
    # without one has no wardrobe, which the stylist reports rather than
    # erroring on. Non-secret and per-member, so it belongs in the roster
    # rather than in settings.
    wardrobe_album: str | None = None

    def can(self, permission: str) -> bool:
        return permission in self.permissions


class Family:
    def __init__(self, members: list[Member]) -> None:
        self._by_sub = {m.sub: m for m in members}

    @classmethod
    def from_yaml(cls, path: Path) -> "Family":
        raw = yaml.safe_load(Path(path).read_text()) or {}
        return cls(
            [
                Member(
                    sub=str(entry["sub"]),
                    name=entry["name"],
                    role=entry["role"],
                    timezone=entry["timezone"],
                    permissions=frozenset(entry.get("permissions", [])),
                    wardrobe_album=entry.get("wardrobe_album") or None,
                )
                for entry in raw.get("members", [])
            ]
        )

    def members(self) -> tuple[Member, ...]:
        """Roster order, for the ambient poll loop. Insertion-ordered dict."""
        return tuple(self._by_sub.values())

    def get(self, sub: str) -> Member:
        try:
            return self._by_sub[sub]
        except KeyError:
            raise UnknownMemberError(f"no family member with subject {sub!r}") from None


@lru_cache(maxsize=1)
def get_family() -> Family:
    return Family.from_yaml(get_settings().family_file)
