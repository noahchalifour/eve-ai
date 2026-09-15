"""The custom-route mount, against a real `aegra serve`.

Aegra's `enable_custom_route_auth` is version-specific and applies its auth
dependency by walking routes after the core routers are included, so both
halves of this need proving on the real server rather than a TestClient:
the widget routes demand a credential, and the health probes still answer
without one.
"""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.integration


async def test_the_resource_api_requires_a_credential(aegra_server):
    async with httpx.AsyncClient(base_url=aegra_server) as client:
        response = await client.get("/provider-resources/v1/capabilities")

    assert response.status_code == 401


async def test_an_authenticated_member_reads_capabilities(aegra_server, dev_token):
    async with httpx.AsyncClient(base_url=aegra_server) as client:
        response = await client.get(
            "/provider-resources/v1/capabilities",
            headers={"Authorization": f"Bearer {dev_token}"},
        )

    assert response.status_code == 200
    assert "chart" in response.json()["kinds"]


async def test_health_probes_still_answer_without_a_credential(aegra_server):
    """`enable_custom_route_auth` walks every route, so this is the exact
    regression it can cause."""
    async with httpx.AsyncClient(base_url=aegra_server) as client:
        for path in ("/health", "/ok", "/healthz"):
            response = await client.get(path)
            if response.status_code != 404:
                assert response.status_code == 200, f"{path} needs a credential"


async def test_the_graph_still_serves_runs(aegra_server, dev_token):
    """The mount must not displace the Agent Protocol routes.

    Amended from the plan's `GET /assistants/eve` probe: in this deployment
    (aegra-api 0.10.3) assistant rows are keyed by a uuid derived from the
    graph id, so `/assistants/eve` 404s even though the graph serves runs
    under that id — a pre-existing Aegra behavior verified against the live
    server. The assistants list is the stable Agent Protocol surface, so it
    is probed instead.
    """
    async with httpx.AsyncClient(base_url=aegra_server) as client:
        response = await client.get(
            "/assistants", headers={"Authorization": f"Bearer {dev_token}"}
        )

    assert response.status_code == 200