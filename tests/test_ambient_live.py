"""One fabricated Home Assistant signal, all the way through: a real REFLEX
verdict, a real eve turn on a real thread, and a real ntfy push.

Opt-in twice, because it spends quota and notifies a phone:

    EVE_LIVE_TESTS=1 \
    EVE_AMBIENT_NTFY_BASE_URL=https://ntfy.example \
    EVE_AMBIENT_NTFY_TOPIC=eve-family-test \
    uv run pytest -m live tests/test_ambient_live.py -v

Requires the compose stack up and `EVE_LITELLM_API_KEY` set.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from eve_ambient.pipeline import handle_signal
from eve_ambient.types import Signal

pytestmark = pytest.mark.live

LIVE = os.environ.get("EVE_LIVE_TESTS") == "1"
NTFY = os.environ.get("EVE_AMBIENT_NTFY_TOPIC")


@pytest.mark.skipif(not LIVE, reason="EVE_LIVE_TESTS is not 1")
@pytest.mark.skipif(not NTFY, reason="no ntfy test topic configured")
async def test_a_water_leak_reaches_a_real_notification(aegra_server):
    """Urgent by design: it is the one shape whose verdict is predictable
    enough to assert on, and it exercises the bypass path end to end."""
    signal = Signal(
        source="home",
        key=f"sensor.basement_water:wet:{datetime.now(UTC).isoformat()}",
        occurred_at=datetime.now(UTC),
        member_sub=None,
        summary="The basement water sensor is reporting water on the floor.",
        payload={"entity_id": "sensor.basement_water", "state": "wet"},
    )
    outcome = await handle_signal(signal)
    assert outcome == "sent"


@pytest.mark.skipif(not LIVE, reason="EVE_LIVE_TESTS is not 1")
async def test_a_non_event_is_filtered_by_the_real_reflex_model(aegra_server):
    """The other half of the contract: the filter has to say no to something
    plainly uninteresting, or the daily cap is doing all the work."""
    signal = Signal(
        source="home",
        key=f"sensor.living_room_lux:41:{datetime.now(UTC).isoformat()}",
        occurred_at=datetime.now(UTC),
        member_sub=None,
        summary="The living room light level changed from 40 to 41 lux.",
        payload={"entity_id": "sensor.living_room_lux", "state": "41"},
    )
    assert await handle_signal(signal) in ("filtered", "vetoed")
