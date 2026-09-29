"""The dashboard grid: sizes, first-fit packing, and layout validation.

Pure module: no I/O, no database, no LangGraph. The client mirrors `pack` and
`validate` in `lib/domain/models/dashboard/dashboard_layout.dart`; a layout
this module accepts is one that client can render without overlap, and the
server re-validates every layout the client saves, because the client is not
a trust boundary.

A layout is in CELLS of a grid `columns` wide and unbounded in height.
Cells are square on the client, so a size is a shape, not a pixel box.
"""

from __future__ import annotations

import re

# The column counts a device may declare: a phone and a tablet. Stored with
# the dashboard, so a layout is always read on the grid it was made for.
COLUMN_CHOICES = frozenset({4, 8})

# The closed size vocabulary, as `WxH` in cells. Small, wide, large and tall:
# the shapes iOS and Android widgets use, and the only ones a tile can snap to.
SIZES: dict[str, tuple[int, int]] = {
    "2x2": (2, 2),
    "4x2": (4, 2),
    "2x4": (2, 4),
    "4x4": (4, 4),
}

MAX_TILES = 12
MAX_ROWS = 96

_DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def valid_device_id(candidate: object) -> bool:
    return isinstance(candidate, str) and bool(_DEVICE_ID.match(candidate))


def fits(x: int, y: int, w: int, h: int, occupied: set[tuple[int, int]], columns: int) -> bool:
    if x < 0 or y < 0 or x + w > columns or y + h > MAX_ROWS:
        return False
    return all((cx, cy) not in occupied for cx in range(x, x + w) for cy in range(y, y + h))


def _claim(x: int, y: int, w: int, h: int, occupied: set[tuple[int, int]]) -> None:
    occupied.update((cx, cy) for cx in range(x, x + w) for cy in range(y, y + h))


def pack(sizes: list[tuple[int, int]], columns: int) -> list[tuple[int, int]]:
    """First-fit, row-major: each tile takes the top-most, then left-most,
    free spot. Order is preserved, so the builder's most important widget
    lands top-left. Returns one `(x, y)` per size."""
    occupied: set[tuple[int, int]] = set()
    placed: list[tuple[int, int]] = []
    for w, h in sizes:
        w = min(w, columns)
        for y in range(MAX_ROWS):
            spot = next((x for x in range(columns - w + 1) if fits(x, y, w, h, occupied, columns)), None)
            if spot is not None:
                _claim(spot, y, w, h, occupied)
                placed.append((spot, y))
                break
        else:
            raise ValueError("the dashboard is full")
    return placed


def compact(tiles: list[tuple[int, int, int, int]], columns: int) -> list[tuple[int, int]]:
    """Gravity: tiles in reading order of their current spot, each dropped
    into the first free spot from the top. Returns one `(x, y)` per tile, in
    the order given. Mirrors the client's `DashboardLayout.compact`."""
    order = sorted(range(len(tiles)), key=lambda i: (tiles[i][1], tiles[i][0]))
    spots = pack([(tiles[i][2], tiles[i][3]) for i in order], columns)
    placed: list[tuple[int, int]] = [(0, 0)] * len(tiles)
    for i, spot in zip(order, spots):
        placed[i] = spot
    return placed


def validate(placements: object, stored: list[dict], columns: int) -> list[dict] | str:
    """The placements a client may save, normalised, or why not.

    `stored` is the dashboard's current layout (each entry carries the
    tile's allowed `sizes`). A client may move, resize within `sizes`, and
    drop tiles; it may not add a tile, invent a size, or overlap two.
    """
    if not isinstance(placements, list):
        return "layout must be a list"
    allowed = {entry["resourceId"]: entry for entry in stored}
    occupied: set[tuple[int, int]] = set()
    seen: set[str] = set()
    normalised: list[dict] = []
    for placement in placements:
        if not isinstance(placement, dict):
            return "each placement must be an object"
        resource_id = placement.get("resourceId")
        if resource_id not in allowed:
            return "layout names a widget this dashboard does not have"
        if resource_id in seen:
            return "layout names a widget twice"
        seen.add(resource_id)
        values = [placement.get(key) for key in ("x", "y", "w", "h")]
        if any(isinstance(v, bool) or not isinstance(v, int) for v in values):
            return "x, y, w and h must be integers"
        x, y, w, h = values
        if f"{w}x{h}" not in allowed[resource_id]["sizes"]:
            return f"{w}x{h} is not a size this widget allows"
        if not fits(x, y, w, h, occupied, columns):
            return "layout overlaps or leaves the grid"
        _claim(x, y, w, h, occupied)
        normalised.append({**allowed[resource_id], "x": x, "y": y, "w": w, "h": h})
    return normalised
