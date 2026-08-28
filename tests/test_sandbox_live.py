"""Checks that are only meaningful against the deployed pod.

Run by hand: `EVE_LIVE_TESTS=1 uv run pytest tests/test_sandbox_live.py -m live`
with kubectl pointed at the cluster. These verify the claims §6.2 makes about
the pod, which no in-process test can.
"""

import os
import subprocess

import pytest

pytestmark = pytest.mark.live

POD = "deploy/eve-sandbox"


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
    """DoD 10. A token here is a path to the cluster API."""
    result = _exec("ls", "/var/run/secrets/kubernetes.io/serviceaccount")
    assert result.returncode != 0, result.stdout


def test_the_pod_cannot_reach_any_external_host():
    """DoD 10. Default-deny egress is the boundary, not a mitigation."""
    result = _exec(
        "python", "-c",
        "import socket;"
        "socket.setdefaulttimeout(5);"
        "socket.create_connection(('example.com', 80))",
    )
    assert result.returncode != 0
    assert "Errno" in result.stderr or "timed out" in result.stderr


def test_the_root_filesystem_is_read_only_except_tmp():
    """DoD 10."""
    assert _exec("sh", "-c", "touch /app/x").returncode != 0
    assert _exec("sh", "-c", "touch /tmp/x && rm /tmp/x").returncode == 0


def test_no_eve_environment_variables_are_present_beyond_the_api_key():
    result = _exec("printenv")
    leaked = [
        line for line in result.stdout.splitlines()
        if line.startswith("EVE_") and not line.startswith("EVE_SANDBOX_")
    ]
    assert leaked == [], leaked
