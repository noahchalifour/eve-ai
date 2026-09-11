"""The built image must actually contain the skills corpus.

`Settings.skills_dir` is the RELATIVE path `skills`, resolved against the
process CWD - `/app` in the image. For the whole life of the deployment the
Dockerfile copied `src`, `prompts`, `family.yaml`, `aegra.json` and
`alembic` but never `skills/`, so `load_skills()` globbed a directory that
did not exist, returned an empty corpus, and `search_skills` answered "No
matching skill or tool found." to every query ever made in production -
while resolving perfectly in any dev checkout, where CWD is the repo root.

That is the exact shape of bug no unit test can catch: every test in
`tests/test_skills_*.py` either points `EVE_SKILLS_DIR` at a tmp_path or
runs from the repo root. Only the artifact itself can be asked.

Marked `docker` for the same reason `test_sandbox_docker_image.py` is: this
runs a real `docker build`, which is far too slow for the default tier. It
is wired into the same `docker-image-test` CI job.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.docker

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
IMAGE_TAG = "eve-ai-skills-image-test:latest"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=True)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


@pytest.fixture(scope="module")
def eve_image():
    if not _docker_available():
        pytest.skip("docker is not available in this environment")
    subprocess.run(
        ["docker", "build", "-f", "Dockerfile", "-t", IMAGE_TAG, str(_REPO_ROOT)],
        check=True,
        timeout=900,
    )
    yield IMAGE_TAG


def _repo_skill_names() -> set[str]:
    return {p.parent.name for p in (_REPO_ROOT / "skills").glob("*/SKILL.md")}


def test_every_repo_skill_is_present_in_the_image(eve_image):
    """Pinned against the repo's own directory listing rather than a
    hardcoded list, so a skill added later is covered without editing this
    test - the failure mode being guarded is "the COPY line is missing", not
    "one particular skill went missing"."""
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", eve_image,
         "-c", "ls /app/skills"],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    shipped = set(result.stdout.split())
    expected = _repo_skill_names()
    assert expected, "the repo itself has no skills/*/SKILL.md to ship"
    assert expected <= shipped, f"missing from image: {sorted(expected - shipped)}"


def test_the_image_resolves_a_non_empty_skills_corpus(eve_image):
    """The end the bug was actually felt at. `ls` proves the files are
    there; this proves `load_skills()` - run with the image's real CWD,
    interpreter and settings - can see them, which is the thing
    `search_skills` depends on."""
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "python", eve_image,
         "-c", "from eve.skills.registry import load_skills; "
               "print(sorted(s.name for s in load_skills()))"],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    assert "build-a-ui" in result.stdout, result.stdout + result.stderr
