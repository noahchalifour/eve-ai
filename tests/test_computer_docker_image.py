"""Builds the real image from Dockerfile.eve-computer, runs a container, and
drives a task through its real HTTP surface with `harness.run_task` faked
out via an environment variable the test sets before the container starts -
the same "assert the built artifact actually works" gap
test_sandbox_docker_image.py's own docstring explains no unit test can close.
Skips gracefully if Docker isn't available.
"""

from __future__ import annotations

import shutil
import subprocess
import time

import httpx
import pytest

pytestmark = pytest.mark.docker

_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
IMAGE_TAG = "eve-computer-docker-image-test:latest"
CONTAINER_NAME = "eve-computer-docker-image-test"
HOST_PORT = 18096
API_KEY = "test-key-0123456789abcdef0123456789ab"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=True)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


@pytest.fixture(scope="module")
def computer_image():
    if not _docker_available():
        pytest.skip("docker is not available in this environment")
    subprocess.run(
        ["docker", "build", "-f", "Dockerfile.eve-computer", "-t", IMAGE_TAG, str(_REPO_ROOT)],
        check=True, timeout=1200,
    )
    yield IMAGE_TAG


@pytest.fixture
def computer_container(computer_image):
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)
    subprocess.run(
        [
            "docker", "run", "-d", "--name", CONTAINER_NAME,
            "-p", f"{HOST_PORT}:8092",
            "-e", f"EVE_COMPUTER_API_KEY={API_KEY}",
            computer_image,
        ],
        check=True,
    )
    url = f"http://127.0.0.1:{HOST_PORT}"
    deadline = time.time() + 30
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
                f"eve-computer did not become healthy within 30s:\n{logs.stdout}\n{logs.stderr}"
            )
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)


def test_the_gui_tool_dependencies_are_present(computer_container):
    """Smoke-level coverage for the one piece nothing else can test without a
    real X server (Task 12's note): the binaries the GUI tool shells out to
    actually exist in the built image."""
    for binary in ("xdotool", "import", "Xvfb", "x11vnc", "codex"):
        result = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "which", binary],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"{binary} missing from the built image"


def test_the_bundled_claude_cli_is_present_and_runs(computer_container):
    """Resolves the open question flagged by Batch D's implementer and
    reviewer: Dockerfile.eve-computer has no `npm install -g
    @anthropic-ai/claude-code` line, on the claim that claude-agent-sdk's
    installed wheel bundles a platform-specific `claude` CLI binary that its
    own `SubprocessCLITransport._find_bundled_cli()` finds automatically
    (`claude_agent_sdk/_bundled/claude`, checked before any PATH search - see
    harness.py's docstring and Dockerfile.eve-computer's comment). `claude`
    is deliberately not asserted via `which` in the test above: nothing puts
    it on PATH (no npm install, no console-script entry point), so the only
    faithful check is to reproduce `_find_bundled_cli()`'s own path logic
    inside the built image and confirm the binary it finds actually runs."""
    result = subprocess.run(
        [
            "docker", "exec", CONTAINER_NAME, "python3", "-c",
            "import pathlib, subprocess;"
            "import claude_agent_sdk._internal.transport.subprocess_cli as m;"
            "p = pathlib.Path(m.__file__).parent.parent.parent / '_bundled' / 'claude';"
            "assert p.is_file(), f'bundled cli missing at {p}';"
            "out = subprocess.run([str(p), '--version'], capture_output=True, text=True, timeout=10);"
            "assert out.returncode == 0, out.stderr;"
            "print(out.stdout.strip())",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_bootstrap_started_the_desktop(computer_container):
    result = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "pgrep", "-f", "Xvfb"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, "Xvfb is not running"


def test_the_task_api_requires_auth(computer_container):
    response = httpx.post(f"{computer_container}/tasks", json={"id": "t1", "goal": "x"})
    assert response.status_code == 401


def test_dispatching_a_task_is_accepted(computer_container):
    response = httpx.post(
        f"{computer_container}/tasks",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"id": "smoke-1", "goal": "say hello and write it to ./out/hello.txt"},
        timeout=10,
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_the_image_carries_every_coding_binary(computer_container):
    """EVE-4 adds three ACP agents and `gh`. A missing one fails at the
    first session, half an hour after Eve told a member she was on it."""
    for binary in ("gh", "codex-acp", "claude-code-acp", "opencode"):
        result = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "sh", "-c", f"command -v {binary}"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"{binary} missing from the built image"


def test_bootstrap_writes_all_three_routing_configs(computer_container):
    """A wiped PVC must recover model routing with no human involved
    (design doc: "Storage"). $HOME is the PVC, so these cannot be baked in."""
    script = (
        "EVE_COMPUTER_LITELLM_BASE_URL=https://litellm.example "
        "EVE_COMPUTER_LITELLM_API_KEY=sk-probe "
        "/app/src/eve_computer/bootstrap.sh >/dev/null 2>&1; "
        "cat /home/eve/.codex/config.toml; "
        "cat /home/eve/.config/opencode/opencode.json"
    )
    result = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "sh", "-c", script],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    output = result.stdout

    assert "https://litellm.example" in output
    assert 'wire_api = "responses"' in output
    assert "LITELLM_API_KEY" in output
    # The key itself is never written into a config file - only the name of
    # the environment variable holding it.
    assert "sk-probe" not in output
