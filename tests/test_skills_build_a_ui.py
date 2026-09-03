"""The skill's property table is a FIFTH copy of the catalog, and the only
one written in prose - no validator can catch it drifting. This test is what
catches it instead."""

from __future__ import annotations

import re
from pathlib import Path

from eve.skills.registry import parse_skill_text
from eve.ui import protocol

SKILL = Path(__file__).resolve().parents[1] / "skills" / "build-a-ui" / "SKILL.md"


def _documented() -> dict[str, set[str]]:
    """Every `- \x60type\x60: prop, prop` line in the skill body."""
    name, _description, body, _specialist = parse_skill_text(SKILL.read_text(), "build-a-ui")
    assert name == "build-a-ui"
    found = {}
    for line in body.splitlines():
        match = re.match(r"^- `([A-Za-z]+)`: (.+)$", line.strip())
        if not match:
            continue
        kind, properties = match.group(1), match.group(2).strip()
        found[kind] = (
            set() if properties == "no properties"
            else {p.strip(" `") for p in properties.split(",")}
        )
    return found


def test_the_skill_documents_every_component_type():
    assert set(_documented()) == set(protocol.CATALOG_IDS)


def test_every_documented_property_matches_the_validator():
    documented = _documented()
    for kind, properties in documented.items():
        assert properties == set(protocol._ALLOWED_PROPERTIES[kind]), kind


def test_the_skill_has_a_description_for_semantic_ranking():
    """`rank_skills` embeds `description or name`, so an empty description
    would make this skill unfindable for every phrasing but its own slug."""
    _name, description, _body, _specialist = parse_skill_text(SKILL.read_text(), "build-a-ui")
    assert len(description) > 40
