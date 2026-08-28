"""Loads the skills index: SKILL.md procedures from disk, MCP tool
descriptions handed in by the caller (eve.skills.mcp_registry), and
Eve-authored procedure rows. Rebuilt on every call rather than cached - the
corpus is a handful of files and process-lifetime metadata, and correctness
(a newly-added SKILL.md showing up without a restart) is worth more than the
cache here.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from eve.settings import get_settings
from eve.skills.types import DynamicToolSpec


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    kind: str  # "procedure" | "mcp_tool"
    content: str
    spec: DynamicToolSpec | None = None


def parse_skill_text(text: str, fallback_name: str) -> tuple[str, str, str]:
    """Split a SKILL.md-shaped document into (name, description, body).

    One parser for two sources: files on disk and Eve-authored procedure rows,
    which serialize to this same shape (eve.skills.authoring). `eve_memory`
    has no description column, and reusing this format is cheaper than adding
    one.
    """
    if text.startswith("---"):
        _, frontmatter, body = text.split("---", 2)
        meta = yaml.safe_load(frontmatter) or {}
    else:
        meta, body = {}, text
    return (
        meta.get("name", fallback_name),
        meta.get("description", ""),
        body.strip(),
    )


def _load_skill_md(path) -> Skill:
    name, description, body = parse_skill_text(path.read_text(), path.parent.name)
    return Skill(name=name, description=description, kind="procedure", content=body)


def load_skills(
    mcp_tools: list[DynamicToolSpec] | None = None,
    authored: list | None = None,
) -> list[Skill]:
    """The skills corpus: SKILL.md files on disk, MCP tool metadata, and
    Eve-authored procedure rows.

    Stays synchronous. `authored` is passed in by search_skills, which does
    the database read, rather than read here - otherwise every caller that
    only wanted the filesystem corpus pays for a round trip.
    """
    skills_dir = get_settings().skills_dir
    procedures = (
        [_load_skill_md(p) for p in sorted(skills_dir.glob("*/SKILL.md"))]
        if skills_dir.exists()
        else []
    )
    for row in authored or []:
        name, description, body = parse_skill_text(
            row.content, row.subject or str(row.id)
        )
        procedures.append(
            Skill(
                name=name, description=description, kind="procedure", content=body
            )
        )
    mcp_skills = [
        Skill(
            name=f"{spec['server_id']}.{spec['tool_name']}",
            description=spec["description"],
            kind="mcp_tool",
            content=spec["description"],
            spec=spec,
        )
        for spec in (mcp_tools or [])
    ]
    return [*procedures, *mcp_skills]
