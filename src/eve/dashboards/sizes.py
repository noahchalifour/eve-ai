"""Which grid sizes a widget may take, from its kind.

Server-declared rather than client-guessed: the kind is what knows how much
room its template needs. A size a kind does not list is one its template was
not designed for, and the layout validator refuses it.
"""

from __future__ import annotations

# (allowed sizes, default size). The default is the first thing the builder
# places; the member can resize within the allowed list.
KIND_SIZES: dict[str, tuple[tuple[str, ...], str]] = {
    "entity": (("2x2", "4x2"), "2x2"),
    # A forecast list and a player's control rows need the height: at Wide
    # they clip on a phone (seen on device), so they start Large.
    "weather": (("4x2", "4x4"), "4x4"),
    "glance": (("4x2", "4x4", "2x4"), "4x2"),
    "media": (("4x2", "4x4"), "4x4"),
    "chart": (("4x2", "4x4"), "4x2"),
}
_CUSTOM = (("2x2", "4x2", "2x4", "4x4"), "4x2")


def allowed_sizes(kind: str) -> tuple[list[str], str]:
    sizes, default = KIND_SIZES.get(kind, _CUSTOM)
    return list(sizes), default
