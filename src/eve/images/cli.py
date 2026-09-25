"""eve-images: maintenance for the image store.

`sweep` is run nightly by a Kubernetes CronJob (infrastructure repo,
kubernetes/apps/eve/base/image-sweep-cronjob.yaml). Reads already refuse
expired rows, so a missed night costs disk, never privacy.
"""

from __future__ import annotations

import argparse
import asyncio

from eve.images import store
from eve.memory.db import close_pool


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sweep", help="delete expired images")
    parser.parse_args()

    async def _run() -> None:
        try:
            deleted = await store.sweep()
            print(f"deleted {deleted} expired image(s)")
        finally:
            await close_pool()

    asyncio.run(_run())
