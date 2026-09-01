"""`eve-wardrobe`: build and read the garment catalogue.

A CLI because the initial bulk load has to be one - a hundred photographs is
never going inside a conversational turn - and because the operator is one
person with a terminal. The stylist's `sync_wardrobe` tool calls the same
`catalog.sync`, bounded by a limit; this is the unbounded caller.

Modelled on `eve-skill` and `eve-tool`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from eve.family import Member, get_family
from eve.memory.db import close_pool
from eve.wardrobe import catalog


def _targets(member: str | None) -> list[Member]:
    """Who to sync. Without `--member`, every member who actually has an
    album - syncing a member with no wardrobe is not an error worth printing
    once per run."""
    members = get_family().members()
    if member is None:
        return [m for m in members if m.wardrobe_album]
    matched = [m for m in members if m.name.lower() == member.lower()]
    if not matched:
        names = ", ".join(m.name for m in members)
        raise SystemExit(f"no family member named {member!r} (have: {names})")
    return matched


async def run_sync(member: str | None, force: bool) -> None:
    for target in _targets(member):
        result = await catalog.sync(target.sub, force=force)
        if result["error"]:
            print(f"{target.name}: {result['error']}")
            continue
        line = (
            f"{target.name}: catalogued {result['catalogued']} garment(s), "
            f"removed {result['removed']}, failed {result['failed']}"
        )
        if result["remaining"]:
            line += f", {result['remaining']} left for the next run"
        print(line)


async def run_list(member: str | None) -> None:
    for target in _targets(member):
        print(f"# {target.name}")
        print(await catalog.render_wardrobe(target.sub))
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    syncer = sub.add_parser("sync", help="catalogue new photos from the Immich album")
    syncer.add_argument("--member", default=None, help="one member by name")
    syncer.add_argument(
        "--force",
        action="store_true",
        help="re-describe every photo, not just new ones (use after editing prompts/wardrobe.md)",
    )

    lister = sub.add_parser("list", help="print the catalogue")
    lister.add_argument("--member", default=None, help="one member by name")

    args = parser.parse_args()

    async def _run() -> None:
        try:
            if args.command == "sync":
                await run_sync(args.member, args.force)
            else:
                await run_list(args.member)
        finally:
            await close_pool()

    try:
        asyncio.run(_run())
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        raise
