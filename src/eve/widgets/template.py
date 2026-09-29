# src/eve/widgets/template.py
"""Model-authored widget templates: checked once at save, rendered on every
refresh with no model.

A template is an `assistant-ui/1.0` component tree plus two template-only
affordances the client never sees (`repeat` on a list, and `$item.` inside
it). Bindings stay the client's own `$data.` syntax, so rendering is mostly
"hand the client the tree and the data"; the server only expands repeats and
patches bindings that did not resolve.
"""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable

from pydantic import BaseModel

from eve.ui import protocol
from eve.widgets.sources.base import SourceType

WIDGET_ALIAS = "widget"
WIDGET_FIELDS = frozenset({"title", "days", "empty_points"})
MAX_TEMPLATE_BYTES = 12_288
MAX_REPEAT = 20
DEFAULT_REPEAT = 10
MISSING = "—"

_DATA = re.compile(r"^\$data((?:\.[A-Za-z_][A-Za-z0-9_]*)+)$")
_ITEM = re.compile(r"^\$item((?:\.[A-Za-z_][A-Za-z0-9_]*)+)$")
_TEMPLATE_ONLY = ("repeat", "limit", "empty")

Sources = dict[str, tuple[SourceType, BaseModel]]


def _strings(properties: dict):
    for key, value in properties.items():
        if isinstance(value, str):
            yield key, value
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, str):
                    yield key, entry


def validate(components: object, sources: Sources, *, action_accepts: Callable[[str, str], bool]) -> str | None:
    """None, or a message that tells the model exactly what to change."""
    if not isinstance(components, list) or not components:
        return "template must be a non-empty list of components"
    if len(json.dumps(components).encode()) > MAX_TEMPLATE_BYTES:
        return f"template is larger than {MAX_TEMPLATE_BYTES} bytes; simplify it"

    declared = ", ".join(sorted(sources)) or "none"
    all_targets = frozenset().union(*(s.targets(p) for s, p in sources.values())) if sources else frozenset()

    def check_data(path: str) -> str | None:
        alias, _, rest = path.lstrip(".").partition(".")
        field = rest.split(".", 1)[0]
        if alias == WIDGET_ALIAS:
            return None if field in WIDGET_FIELDS else f"$data.widget.{field} does not exist ({sorted(WIDGET_FIELDS)})"
        if alias not in sources:
            return f"binding $data{path} names source {alias!r}, but the declared sources are: {declared}"
        source, _ = sources[alias]
        if field not in source.fields:
            return f"source {alias!r} ({source.name}) has no field {field!r}; fields: {sorted(source.fields)}"
        return None

    def visit(node: object, repeat: tuple[SourceType, BaseModel, str] | None) -> str | None:
        if not isinstance(node, dict):
            return "every component must be an object"
        properties = node.get("properties") or {}
        if not isinstance(properties, dict):
            return f"component {node.get('id')!r}: properties must be an object"

        child_repeat = repeat
        if "repeat" in properties:
            if node.get("type") != "list":
                return f"component {node.get('id')!r}: only a list may repeat"
            match = _DATA.match(str(properties["repeat"]))
            alias, _, field = (match.group(1).lstrip(".") if match else "").partition(".")
            if not match or alias not in sources or field not in sources[alias][0].item_fields:
                lists = {a: sorted(s.item_fields) for a, (s, _) in sources.items() if s.item_fields}
                return f"repeat must be $data.<alias>.<list field>; repeatable fields: {lists}"
            limit = properties.get("limit", DEFAULT_REPEAT)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_REPEAT:
                return f"limit must be an integer 1-{MAX_REPEAT}"
            if "empty" in properties and not isinstance(properties["empty"], str):
                return "empty must be a string"
            child_repeat = (*sources[alias], field)

        for key, value in _strings(properties):
            if key in _TEMPLATE_ONLY:
                continue
            if not value.startswith(("$data", "$item")) and ("$data." in value or "$item." in value):
                # A binding is a whole value; the client never interpolates
                # one into a sentence, so this would render as literal text.
                return (f"{key} {value!r} embeds a binding in text; a binding must be the whole value. "
                        "Put the words in one text component and the binding in its own")
            if value.startswith("$data"):
                if not _DATA.match(value):
                    return f"malformed binding {value!r}"
                error = check_data(_DATA.match(value).group(1))
                if error:
                    return error
            elif value.startswith("$item"):
                if repeat is None:
                    return f"{value} is only legal inside a list with repeat"
                keys = repeat[0].item_fields.get(repeat[2]) or frozenset()
                item_key = value.split(".", 2)[1] if _ITEM.match(value) else ""
                if keys and item_key not in keys:
                    return f"{value}: items of {repeat[2]} have keys {sorted(keys)}"

        action = properties.get("actionId")
        if isinstance(action, str) and action not in protocol.ACTION_IDS and action != "widget.setRange":
            target = properties.get("actionValue")
            if isinstance(target, str) and target.startswith("$item"):
                if repeat is None or not repeat[0].targets(repeat[1]):
                    return f"{action}: an $item target needs a repeat over a source with targets"
            elif not isinstance(target, str) or target not in all_targets:
                return (f"{action} targets {target!r}, which no source of this widget declares; "
                        f"declared targets: {sorted(all_targets)}")
            elif not action_accepts(action, target):
                return f"action {action!r} does not exist or cannot act on {target!r}"

        for child in node.get("children") or []:
            error = visit(child, child_repeat)
            if error:
                return error
        return None

    for component in components:
        error = visit(component, None)
        if error:
            return error

    probe = _probe(components)
    error = protocol.validate_operation({"protocol": protocol.PROTOCOL, "op": "create", "surface": {
        "surfaceId": "widget:probe", "catalogId": "column", "catalogVersion": protocol.CATALOG_VERSION,
        "components": probe, "data": {}, "localState": {}}}, widget=True)
    return f"template failed catalog validation: {error}" if error else None


def _probe(components: list) -> list:
    """The tree with template-only syntax replaced by legal stand-ins, so the
    catalog validator judges everything else exactly as the client will."""
    def fix(node):
        node = dict(node)
        properties = {k: v for k, v in (node.get("properties") or {}).items() if k not in _TEMPLATE_ONLY}
        node["properties"] = {
            k: ("item" if isinstance(v, str) and v.startswith("$item") else v) for k, v in properties.items()
        }
        node["children"] = [fix(child) for child in node.get("children") or []]
        return node
    return [fix(component) for component in components]


def _lookup(data: dict, path: str) -> tuple[bool, object]:
    cursor: object = data
    for segment in path.lstrip(".").split("."):
        if isinstance(cursor, dict) and segment in cursor:
            cursor = cursor[segment]
        else:
            return False, None
    return True, cursor


def _display(value: object) -> str:
    if value is None:
        return MISSING
    if isinstance(value, bool):
        return "On" if value else "Off"
    return str(value)


def render(components: list, data: dict) -> tuple[list, list[str]]:
    """The tree the client receives, and any degradations (`binding`)."""
    problems: list[str] = []

    def substitute_item(node: dict, item: dict, suffix: str) -> dict:
        node = copy.deepcopy(node)
        node["id"] = f"{node['id']}{suffix}"
        properties = node.get("properties") or {}
        for key, value in list(properties.items()):
            if isinstance(value, str) and _ITEM.match(value):
                found, resolved = _lookup(item, _ITEM.match(value).group(1))
                if key == "actionValue" and found and isinstance(resolved, (str, int, float, bool)):
                    properties[key] = resolved
                else:
                    properties[key] = _display(resolved) if found else MISSING
        node["children"] = [substitute_item(child, item, suffix) for child in node.get("children") or []]
        return node

    def expand(node: dict) -> dict:
        node = copy.deepcopy(node)
        properties = node.get("properties") or {}
        repeat = properties.pop("repeat", None)
        limit = properties.pop("limit", DEFAULT_REPEAT)
        empty = properties.pop("empty", None)
        if repeat is not None:
            _, items = _lookup(data, _DATA.match(repeat).group(1))
            items = [item for item in items if isinstance(item, dict)][:limit] if isinstance(items, list) else []
            template_children = node.get("children") or []
            children = [
                substitute_item(child, item, f"_{index}")
                for index, item in enumerate(items)
                for child in template_children
            ]
            if not children and empty:
                children = [{"id": f"{node['id']}_empty", "type": "text", "properties": {"text": empty}, "children": []}]
            node["children"] = children
        node["children"] = [expand(child) for child in node.get("children") or []]
        for key, value in list(properties.items()):
            if isinstance(value, str) and _DATA.match(value):
                found, _ = _lookup(data, _DATA.match(value).group(1))
                if not found:
                    problems.append("binding")
                    properties[key] = "$data.widget.empty_points" if key == "points" else MISSING
        node["properties"] = properties
        return node

    return [expand(component) for component in components], problems
