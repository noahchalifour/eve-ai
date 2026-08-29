"""The one test that verifies the *built Docker image* can actually import
and run its own package - the exact gap that let Critical 1 (whole-branch
review) ship: `sys.executable -I -m eve_sandbox.runner` silently discards
PYTHONPATH under `-I`, so every real /invoke call against the built image
failed with ModuleNotFoundError, while a dev checkout's editable-install
.pth file masked the bug regardless of PYTHONPATH or isolation flags (see
test_sandbox_http_dispatch.py's docstring - that test exercises the same
HTTP + subprocess dispatch path, but only ever against this dev checkout,
so it could not and did not catch Critical 1).

This test builds the real image from Dockerfile.eve-sandbox, runs a
container from it, and hits its /invoke endpoint over HTTP exactly the way
eve's tools_client does in production. Short of an actual cluster deploy,
this is the only automated coverage of "does the artifact GitHub Actions
publishes actually work."

Marked `docker` rather than `integration`: unlike the docker-compose-backed
integration tier (already-running services), this test itself runs `docker
build`, and a cold build takes on the order of a minute - too slow to want
in the default `uv run pytest` tier, or even every integration run. It is
wired into its own CI job (.github/workflows/build.yml's `docker-image-test`,
run on every push/PR) rather than left to only the tag-gated `image` job,
which builds and publishes this same image but never asserts it works.

Skips gracefully if Docker isn't available, mirroring how the `live` marker
skips when EVE_LIVE_TESTS isn't set.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import time

import httpx
import pytest

pytestmark = pytest.mark.docker

_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
IMAGE_TAG = "eve-sandbox-docker-image-test:latest"
CONTAINER_NAME = "eve-sandbox-docker-image-test"
HOST_PORT = 18094
API_KEY = "test-key-0123456789abcdef0123456789ab"
PURE = "def run(arguments):\n    return {'n': arguments['a'] + 1}\n"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10, check=True
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


@pytest.fixture(scope="module")
def sandbox_image():
    if not _docker_available():
        pytest.skip("docker is not available in this environment")
    subprocess.run(
        [
            "docker", "build",
            "-f", "Dockerfile.eve-sandbox",
            "-t", IMAGE_TAG,
            str(_REPO_ROOT),
        ],
        check=True,
        timeout=900,
    )
    yield IMAGE_TAG


@pytest.fixture
def sandbox_container(sandbox_image):
    # Belt and suspenders: a previous run that crashed before its own
    # teardown could otherwise leave a stale container squatting on the name
    # or the port.
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)
    subprocess.run(
        [
            "docker", "run", "-d",
            "--name", CONTAINER_NAME,
            "-p", f"{HOST_PORT}:8091",
            "-e", f"EVE_SANDBOX_API_KEY={API_KEY}",
            sandbox_image,
        ],
        check=True,
    )
    url = f"http://127.0.0.1:{HOST_PORT}"
    deadline = time.time() + 20
    try:
        while time.time() < deadline:
            try:
                if httpx.get(f"{url}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            logs = subprocess.run(
                ["docker", "logs", CONTAINER_NAME], capture_output=True, text=True
            )
            raise RuntimeError(
                "eve-sandbox container did not become healthy within 20s:\n"
                f"{logs.stdout}\n{logs.stderr}"
            )
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)


def test_the_built_image_can_invoke_a_tool(sandbox_container):
    """Critical 1 regression, at the actual image level. Before the fix,
    every /invoke call against the built image (as opposed to a dev
    checkout) failed with a ModuleNotFoundError, because `-I` discards
    PYTHONPATH and the built image has no editable-install .pth to fall
    back on. This asserts the real, from-scratch image can import
    eve_sandbox and produce a correct result over HTTP."""
    source_sha256 = hashlib.sha256(PURE.encode()).hexdigest()
    response = httpx.post(
        f"{sandbox_container}/invoke",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "tool": "amortise",
            "arguments": {"a": 41},
            "source": PURE,
            "source_sha256": source_sha256,
        },
        timeout=10,
    )
    assert response.status_code == 200
    assert response.json() == {"result": {"n": 42}}
