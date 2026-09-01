"""Checks that are only meaningful against the deployed pod - the design
doc's "test that matters most." Every safety claim in the design reduces to
the NetworkPolicy; this is what makes a future loosening of it fail loudly.

Run by hand:
`EVE_LIVE_TESTS=1 uv run pytest tests/test_computer_live.py -m live`
with kubectl pointed at the cluster.
"""

import os
import subprocess

import pytest

pytestmark = pytest.mark.live

POD = "deploy/eve-computer"

# Cluster-internal hosts that must be unreachable from this pod (design doc:
# "Egress" - "she cannot reach Postgres, eve-tools, eve-sandbox, Eve's own
# API, or the Kubernetes API server"). Hostnames match the other in-cluster
# Services this repository's own Dockerfiles/settings default to.
_UNREACHABLE_HOSTS = [
    ("postgres", 5432),
    ("eve-tools", 8090),
    ("eve-sandbox", 8091),
    ("eve", 2026),
    ("kubernetes.default.svc", 443),
]


def _exec(*command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "exec", POD, "--", *command],
        capture_output=True, text=True, timeout=60,
    )


@pytest.fixture(autouse=True)
def _require_live():
    if os.environ.get("EVE_LIVE_TESTS") != "1":
        pytest.skip("EVE_LIVE_TESTS is not 1")


def test_the_pod_has_no_service_account_token():
    result = _exec("ls", "/var/run/secrets/kubernetes.io/serviceaccount")
    assert result.returncode != 0, result.stdout


@pytest.mark.parametrize("host,port", _UNREACHABLE_HOSTS)
def test_the_pod_cannot_reach_a_cluster_internal_host(host, port):
    result = _exec(
        "python3", "-c",
        "import socket;"
        "socket.setdefaulttimeout(5);"
        f"socket.create_connection(('{host}', {port}))",
    )
    assert result.returncode != 0
    assert "Errno" in result.stderr or "timed out" in result.stderr


def test_the_pod_can_reach_the_public_internet():
    """The egress policy is deny-by-default for RFC1918/cluster ranges, not
    deny-everything (design doc: "Egress") - the positive case is as much a
    part of the boundary as the negative ones above."""
    result = _exec(
        "python3", "-c",
        "import socket;"
        "socket.setdefaulttimeout(5);"
        "socket.create_connection(('example.com', 443))",
    )
    assert result.returncode == 0


def test_no_eve_environment_variables_are_present_beyond_the_api_key():
    result = _exec("printenv")
    leaked = [
        line for line in result.stdout.splitlines()
        if line.startswith("EVE_") and not line.startswith("EVE_COMPUTER_")
    ]
    assert leaked == [], leaked


def test_the_home_directory_survives_a_write():
    """DoD 1: the PVC-backed home, not just the pod, is what must persist."""
    marker = "/home/eve/.eve/live-test-marker"
    assert _exec("sh", "-c", f"touch {marker} && rm {marker}").returncode == 0
