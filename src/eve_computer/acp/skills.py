"""Noah's agent-skills, linked where every ACP coding agent looks (EVE-45).

The repository is `skills/<domain>/<name>/SKILL.md`; the agents want a flat
`<dir>/<name>/SKILL.md`. On his laptop the answer is a symlink per skill
into `~/.agents/skills` and `~/.claude/skills`, and this box does the same:
dsh (dsh-skill-filesystem's user-agents root) and Codex read the first,
Claude Code (claude-code-acp's `settingSources: ["user", ...]`) reads the
second, OpenCode reads both. All four inherit HOME from the ACP transport,
so no registry change is needed - the skills are simply where they look.

WHY NOT IN THE IMAGE. The repository is private and the only GitHub
credential is Eve's own `gh` login on the PVC, which a build never sees;
and skills change far more often than Eve ships.

WHY ONLY OUR OWN LINKS ARE TOUCHED. The link directories are also where
`npx skills add` and Eve herself install things. A name already taken by
something this module did not create is left alone and logged, and pruning
only removes links that point into our own clone.

WHY BEST-EFFORT. Same as harness.py's profile pull: an unreachable
repository must cost sessions their skills, not the box its desktop. A
fetch failure keeps the last good clone linked.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from eve_computer.settings import get_computer_settings

logger = logging.getLogger(__name__)


def _git(*args: str) -> None:
    # No prompt: before `gh auth login` a private clone would otherwise ask
    # for a username on a terminal nobody is at, and stall the pod start.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    subprocess.run(["git", *args], check=True, capture_output=True, timeout=120, env=env)


def _update_clone(clone: Path, repo: str, branch: str) -> None:
    try:
        if (clone / ".git").exists():
            _git("-C", str(clone), "fetch", "--depth", "1", "origin", branch)
            _git("-C", str(clone), "reset", "--hard", "FETCH_HEAD")
        else:
            shutil.rmtree(clone, ignore_errors=True)
            clone.parent.mkdir(parents=True, exist_ok=True)
            _git("clone", "--depth", "1", "--branch", branch, repo, str(clone))
    except (subprocess.SubprocessError, OSError):
        logger.warning("could not update agent-skills; using the last good copy", exc_info=True)
        if not (clone / ".git").exists():
            shutil.rmtree(clone, ignore_errors=True)


def _discover(clone: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for skill_md in sorted((clone / "skills").glob("*/*/SKILL.md")):
        directory = skill_md.parent
        if directory.name in found:
            logger.warning("duplicate skill name %s; keeping %s", directory.name, found[directory.name])
            continue
        found[directory.name] = directory
    return found


def _ours(entry: Path, clone: Path) -> bool:
    return entry.is_symlink() and clone.resolve() in entry.resolve().parents


def _link(link_dir: Path, clone: Path, found: dict[str, Path]) -> None:
    link_dir.mkdir(parents=True, exist_ok=True)
    for entry in link_dir.iterdir():
        if entry.name not in found and _ours(entry, clone):
            entry.unlink()
    for name, target in found.items():
        entry = link_dir / name
        if _ours(entry, clone):
            entry.unlink()
        elif entry.is_symlink() or entry.exists():
            logger.warning("%s already exists and is not ours; leaving it", entry)
            continue
        entry.symlink_to(target.resolve(), target_is_directory=True)


def sync() -> list[str]:
    """Clone or update the repository, then link its skills. Returns the
    skill names linked; empty when disabled or never cloned."""
    settings = get_computer_settings()
    if not settings.agent_skills_repo:
        return []
    clone = Path(settings.agent_skills_dir)
    _update_clone(clone, settings.agent_skills_repo, settings.agent_skills_branch)
    if not clone.exists():
        return []
    found = _discover(clone)
    for link_dir in settings.agent_skills_link_dirs:
        try:
            _link(Path(link_dir), clone, found)
        except OSError:
            logger.warning("could not link agent-skills into %s", link_dir, exc_info=True)
    return list(found)
