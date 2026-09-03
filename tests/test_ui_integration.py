"""Integration coverage for the dynamic chat UI over the real HTTP boundary:
the graph, a real (locally-run) eve-tools process, and a stub Home Assistant
behind it. Only the model is faked.

Requires `docker compose -f docker-compose.test.yml up -d`? No - this tier
needs neither Postgres nor Redis, only the `eve_tools_server` fixture, which
starts eve-tools and the stub HA itself. Marked `integration` because it binds
real ports and spawns real processes.

Weather surface integration tests were deleted in Task 1 (feat(ui)!: delete the
weather surface). This file is currently empty, pending future dynamic-surface
integration coverage - nothing in the plan schedules that work yet.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration
