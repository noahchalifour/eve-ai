"""Loads the skills index: SKILL.md procedures from disk, and MCP tool
descriptions handed in by the caller (eve.skills.mcp_registry). Rebuilt on
every call rather than cached - the corpus is a handful of files and
process-lifetime metadata, and correctness (a newly-added SKILL.md showing
up without a restart) is worth more than the cache here.
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


def _load_skill_md(path) -> Skill:
    text = path.read_text()
    if text.startswith("---"):
        _, frontmatter, body = text.split("---", 2)
        meta = yaml.safe_load(frontmatter) or {}
    else:
        meta, body = {}, text
    return Skill(
        name=meta.get("name", path.parent.name),
        description=meta.get("description", ""),
        kind="procedure",
        content=body.strip(),
    )


def load_skills(mcp_tools: list[DynamicToolSpec] | None = None) -> list[Skill]:
    skills_dir = get_settings().skills_dir
    procedures = (
        [_load_skill_md(p) for p in sorted(skills_dir.glob("*/SKILL.md"))]
        if skills_dir.exists()
        else []
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
