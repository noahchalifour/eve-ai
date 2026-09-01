"""The one Immich check mocks cannot do: the real album listing's shape.

The whole-branch review for EVE-20 ruled this a release gate - MockTransport
verifies this client's parsing, not Immich's actual response contract, and
`catalog._album_asset_list` consumes exactly the fields asserted here.

Opt-in twice, because it needs the real Immich instance and its API key:

    EVE_LIVE_TESTS=1 \
    EVE_TOOLS_IMMICH_URL=https://immich.example \
    EVE_TOOLS_IMMICH_API_KEY=<read-scoped key> \
    EVE_LIVE_IMMICH_ALBUM_ID=<a real album id> \
    uv run pytest -m live tests/test_eve_tools_immich_live.py -v
"""

from __future__ import annotations

import os

import pytest

from eve_tools import immich

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("EVE_LIVE_TESTS") != "1",
        reason="set EVE_LIVE_TESTS=1 to run against the real Immich",
    ),
]

ALBUM_ID = os.environ.get("EVE_LIVE_IMMICH_ALBUM_ID")


@pytest.mark.skipif(
    not ALBUM_ID, reason="set EVE_LIVE_IMMICH_ALBUM_ID to a real album id"
)
async def test_the_real_album_listing_carries_the_fields_the_catalogue_reads():
    result = await immich.album_assets(ALBUM_ID)

    assert isinstance(result["truncated"], bool)
    for asset in result["assets"]:
        assert set(asset) == {"id", "filename"}
        assert asset["id"]
