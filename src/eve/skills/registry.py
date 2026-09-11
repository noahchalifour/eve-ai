"""Loads the skills index: SKILL.md procedures from disk, MCP tool
descriptions handed in by the caller (eve.skills.mcp_registry), and
Eve-authored procedure rows. Rebuilt on every call rather than cached - the
corpus is a handful of files and process-lifetime metadata, and correctness
(a newly-added SKILL.md showing up without a restart) is worth more than the
cache here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from eve.settings import get_settings
from eve.skills.types import DynamicToolSpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    kind: str  # "procedure" | "mcp_tool"
    content: str
    spec: DynamicToolSpec | None = None
    # None means the skill is Eve's own. A name means it belongs to that
    # specialist alone: Eve's search filters it out, and that specialist's
    # search filters everything else out (design doc, "Scoped skills").
    specialist: str | None = None


def parse_skill_text(
    text: str, fallback_name: str
) -> tuple[str, str, str, str | None]:
    """Split a SKILL.md-shaped document into
    (name, description, body, specialist).

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
        meta.get("specialist") or None,
    )


def _load_skill_md(path) -> Skill:
    name, description, body, specialist = parse_skill_text(
        path.read_text(), path.parent.name
    )
    return Skill(
        name=name,
        description=description,
        kind="procedure",
        content=body,
        specialist=specialist,
    )


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
    # Absolute, because `skills_dir` defaults to the RELATIVE path `skills`
    # and so resolves against the process CWD. A deployment that forgets to
    # ship the directory (the eve-ai image did, for its whole life) returns
    # an empty corpus here and `search_skills` answers "No matching skill or
    # tool found." to every query - indistinguishable, without this line,
    # from a corpus that simply had no good match.
    if not skills_dir.exists():
        logger.warning(
            "skills directory not found at %s (cwd %s); no filesystem "
            "procedures will be searchable",
            skills_dir.absolute(),
            Path.cwd(),
        )
        procedures = []
    else:
        procedures = [_load_skill_md(p) for p in sorted(skills_dir.glob("*/SKILL.md"))]
        if not procedures:
            logger.warning(
                "skills directory %s contains no */SKILL.md files",
                skills_dir.absolute(),
            )
    for row in authored or []:
        name, description, body, specialist = parse_skill_text(
            row.content, row.subject or str(row.id)
        )
        procedures.append(
            Skill(
                name=name,
                description=description,
                kind="procedure",
                content=body,
                specialist=specialist,
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
