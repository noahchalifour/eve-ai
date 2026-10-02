"""Lists (ENG-372) on the generic record store (ADR 0019): no new table.

A list is the collection `list.<slug>`; each item is one record whose `key`
is the normalised item text, so adding "Milk" twice is one item and removing
"milk" needs no lookup first. Payload: `{"item": <as said>}`.

Personal lists live under the member's own sub. Household lists (groceries)
live under one shared owner, `HOUSEHOLD_OWNER`, readable by every member and
writable with `memory.write_shared` - the grant that already means "may write
household state". The owner is a sentinel no OIDC subject can collide with.
"""

from __future__ import annotations

import logging
import re

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from eve.records import store
from eve.specialists.permissions import permission_denial

logger = logging.getLogger(__name__)

HOUSEHOLD_OWNER = "household:shared"
SHARED_WRITE = "memory.write_shared"
PREFIX = "list."
MAX_ITEMS_PER_CALL = 50
MAX_ITEM_CHARS = 200
SCOPES = ("me", "household")


class ListError(ValueError):
    pass


class ListDenied(ListError):
    """Carries `permission_denial`'s own sentence, returned verbatim."""


def slug(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    cleaned = cleaned.removesuffix("-list") if cleaned != "list" else cleaned
    if not cleaned:
        raise ListError("the list needs a name, like 'groceries'")
    return cleaned[:60]


def item_key(item: str) -> str:
    return re.sub(r"\s+", " ", item.strip().lower())


def _member(config: RunnableConfig) -> dict:
    return (config.get("configurable") or {}).get("member") or {}


def _owner(member: dict, scope: str, *, writing: bool) -> str:
    if scope not in SCOPES:
        raise ListError("scope must be 'me' or 'household'")
    if scope == "me":
        return member["sub"]
    if writing:
        denial = permission_denial(member.get("permissions") or [], SHARED_WRITE)
        if denial:
            raise ListDenied(denial)
    return HOUSEHOLD_OWNER


def _clean_items(items: list[str]) -> list[str]:
    if isinstance(items, str):
        items = [items]
    cleaned = []
    seen = set()
    for raw in items or []:
        text = re.sub(r"\s+", " ", str(raw)).strip()
        if not text:
            continue
        if len(text) > MAX_ITEM_CHARS:
            raise ListError(f"items are limited to {MAX_ITEM_CHARS} characters")
        if item_key(text) in seen:
            continue
        seen.add(item_key(text))
        cleaned.append(text)
    if not cleaned:
        raise ListError("no items given")
    if len(cleaned) > MAX_ITEMS_PER_CALL:
        raise ListError(f"at most {MAX_ITEMS_PER_CALL} items at a time")
    return cleaned


def _label(name: str, scope: str) -> str:
    return f"the household {name} list" if scope == "household" else f"your {name} list"


@tool
async def list_add(list_name: str, items: list[str], config: RunnableConfig, scope: str = "me") -> str:
    """Add items to a list, e.g. list_name="groceries", items=["milk", "eggs"].
    scope="household" for a list the whole family shares (groceries, chores);
    "me" (default) for the member's own."""
    member = _member(config)
    try:
        name = slug(list_name)
        owner = _owner(member, scope, writing=True)
        cleaned = _clean_items(items)
    except ListDenied as exc:
        return str(exc)
    except ListError as exc:
        return f"error: {exc}"
    added, existing = [], []
    try:
        for text in cleaned:
            result = await store.append(owner, PREFIX + name, {"item": text}, key=item_key(text))
            (existing if result["deduped"] else added).append(text)
    except Exception as exc:
        logger.warning("list_add failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"
    parts = []
    if added:
        parts.append(f"Added {', '.join(added)} to {_label(name, scope)}.")
    if existing:
        parts.append(f"Already on it: {', '.join(existing)}.")
    return " ".join(parts)


@tool
async def list_show(list_name: str, config: RunnableConfig, scope: str = "me") -> str:
    """Show what is on a list. Pass list_name="*" to see which lists exist."""
    member = _member(config)
    try:
        owner = _owner(member, scope, writing=False)
        if list_name.strip() == "*":
            names = [c[len(PREFIX):] for c in await store.collections_with_prefix(owner, PREFIX)]
            if not names:
                return f"There are no {'household' if scope == 'household' else 'personal'} lists yet."
            return "Lists: " + ", ".join(names)
        name = slug(list_name)
    except ListError as exc:
        return f"error: {exc}"
    except Exception as exc:
        logger.warning("list_show failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"
    try:
        rows = await store.query(owner, PREFIX + name, limit=store.MAX_LIMIT)
    except Exception as exc:
        logger.warning("list_show failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"
    if not rows:
        return f"{_label(name, scope).capitalize()} is empty."
    # Oldest first: the order things were added is the order people read a list.
    items = [str((row.get("payload") or {}).get("item") or row.get("key")) for row in reversed(rows)]
    return f"{_label(name, scope).capitalize()}:\n" + "\n".join(f"- {item}" for item in items)


@tool
async def list_remove(list_name: str, items: list[str], config: RunnableConfig, scope: str = "me") -> str:
    """Take items off a list (bought, done, no longer needed)."""
    member = _member(config)
    try:
        name = slug(list_name)
        owner = _owner(member, scope, writing=True)
        cleaned = _clean_items(items)
    except ListDenied as exc:
        return str(exc)
    except ListError as exc:
        return f"error: {exc}"
    try:
        removed_keys = set(await store.delete_keys(owner, PREFIX + name, [item_key(t) for t in cleaned]))
    except Exception as exc:
        logger.warning("list_remove failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"
    removed = [t for t in cleaned if item_key(t) in removed_keys]
    missing = [t for t in cleaned if item_key(t) not in removed_keys]
    parts = []
    if removed:
        parts.append(f"Removed {', '.join(removed)} from {_label(name, scope)}.")
    if missing:
        parts.append(f"Not on it: {', '.join(missing)}.")
    return " ".join(parts)


@tool
async def list_clear(list_name: str, config: RunnableConfig, scope: str = "me") -> str:
    """Empty a list entirely. Only when the member clearly asks to clear it."""
    member = _member(config)
    try:
        name = slug(list_name)
        owner = _owner(member, scope, writing=True)
    except ListDenied as exc:
        return str(exc)
    except ListError as exc:
        return f"error: {exc}"
    try:
        count = await store.clear(owner, PREFIX + name)
    except Exception as exc:
        logger.warning("list_clear failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"
    return f"Cleared {_label(name, scope)} ({count} item{'s' if count != 1 else ''})."
