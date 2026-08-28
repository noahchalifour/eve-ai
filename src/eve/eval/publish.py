"""Best-effort Langfuse upload.

Langfuse is a publishing target, never a dependency (ADR 0009). Everything
here swallows its own failures: the scores are already durable in
eve_eval_run by the time this runs, and the gate reads that, not this.

Langfuse exists in this design for one thing the local table does not give
cheaply - run-over-run comparison in a UI nobody had to build.
"""

from __future__ import annotations

import logging

from eve.eval.types import DatasetItem
from eve.settings import get_settings

logger = logging.getLogger(__name__)


def _client(**kwargs):
    from langfuse import Langfuse

    return Langfuse(**kwargs)


async def publish_run(
    dataset: str,
    arm: str,
    items: list[DatasetItem],
    results: dict,
    scores: dict,
) -> bool:
    """True on success. Never raises."""
    settings = get_settings()
    try:
        client = _client(host=settings.langfuse_host)
        client.create_dataset(name=dataset)
        for item in items:
            client.create_dataset_item(
                dataset_name=dataset,
                id=item.id,
                input=item.input,
                expected_output=item.expected,
                metadata={"arm": arm, "canary": item.canary},
            )
        for name, value in scores.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                client.create_score(
                    name=f"{dataset}.{arm}.{name}", value=float(value)
                )
        client.flush()
    except Exception:
        logger.warning(
            "could not publish the eval run to Langfuse; scores are already "
            "recorded locally and the gate does not read Langfuse",
            exc_info=True,
        )
        return False
    return True
