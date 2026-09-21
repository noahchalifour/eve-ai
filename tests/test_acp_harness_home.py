"""The DSH harness home: what `prepare` writes, and why each part of it
survives a profile pulled from git.

Every test here is about ONE hazard. The harness home is shared with a
configuration repository Noah pushes from his laptop (EVE-24: "It should
pull my profile from git"), and that repository is allowed to carry a
`settings.yaml` and a home-level `cordis.patch.yml` of its own. Those files
are his preferences, not this box's routing, and the box must come up
routed at LiteLLM whether the pull succeeded, failed, or delivered a
profile that names a provider this deployment has never heard of.

The mechanism is layering, verified against a real `dsh` in
tests/test_acp_dsh_live.py: `--patch` is the LAST layer the launcher
applies, after every bundle, after the profile's own patch file, and after
the home-level one. So the route lives in a file only this box writes and
only this box passes, and the pulled profile keeps everything it is for.
"""

from __future__ import annotations

import json

import pytest
import yaml

from eve_computer.acp import harness


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    monkeypatch.setenv("EVE_COMPUTER_LITELLM_BASE_URL", "https://litellm.example")
    monkeypatch.setenv("EVE_COMPUTER_LITELLM_API_KEY", "sk-test")
    monkeypatch.setenv("EVE_COMPUTER_DSH_HOME", str(tmp_path / "harness"))
    monkeypatch.setenv("EVE_COMPUTER_DSH_PROFILE_REPO", "")
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    yield
    get_computer_settings.cache_clear()


def _patch_rows(path):
    # harness.Loader, not safe_load: the file is the launcher's entry-list
    # dialect, where `!!js` scalars are expressions rather than strings.
    return yaml.load(path.read_text(), Loader=harness.Loader)


def test_prepare_writes_a_bootable_acp_profile(tmp_path):
    home = harness.prepare()

    manifest = json.loads((home / "profiles" / "acp" / "package.json").read_text())
    assert manifest["dsh"]["profile"]["bundles"] == [
        "@deepseek-ai/dsh-base",
        "@deepseek-ai/dsh-acp-app",
    ]


def test_the_route_is_a_patch_file_outside_the_synced_profile(tmp_path):
    """The whole design in one assertion.

    `dsh --patch <file>` is the last layer; a pulled profile cannot outrank
    it. If this route ever moves INTO profiles/acp/cordis.patch.yml, a
    `harness-sync pull` carrying that same path would overwrite this box's
    routing with a laptop's, and every session would fail at its first
    prompt with a provider nobody configured here.
    """
    home = harness.prepare()

    assert harness.route_patch_path().exists()
    assert "profiles" not in harness.route_patch_path().relative_to(home).parts


def test_the_route_points_at_litellm_and_names_the_key_by_reference():
    harness.prepare()
    rows = {row["id"]: row for row in _patch_rows(harness.route_patch_path())}

    provider = rows["llm-pi-ai"]["config"]["providers"][harness.PROVIDER]
    assert provider["baseURL"] == "https://litellm.example/v1"
    assert provider["api"] == "openai-completions"
    # The key is a reference resolved per request, never the key itself: this
    # file lands on the PVC and `harness-sync push` would otherwise carry it.
    assert provider["apiKeyEnv"] == harness.API_KEY_VAR
    assert "sk-test" not in harness.route_patch_path().read_text()


def test_the_model_is_an_expression_because_eve_chooses_it_per_session():
    """Agent and model are both chosen per task (spec), but `dsh` takes no
    `--model` flag - the model is config. A literal here would pin every
    session to one model; the expression reads the variable the registry
    hands each subprocess, so one file serves every model Eve names."""
    harness.prepare()
    rows = {row["id"]: row for row in _patch_rows(harness.route_patch_path())}

    assert rows["acp"]["config"]["provider"] == harness.PROVIDER
    assert rows["acp"]["config"]["model"].startswith("!!js")
    assert harness.MODEL_VAR in rows["acp"]["config"]["model"]
    # The route's model list is built from the same variable, because pi-ai
    # refuses a model the route does not declare ("has no configured model").
    declared = rows["llm-pi-ai"]["config"]["providers"][harness.PROVIDER]["models"]
    assert harness.MODEL_VAR in declared[0]["id"]


def test_prepare_is_idempotent_and_rewrites_the_route():
    """Called on every container start, like bootstrap.sh's config templates:
    $HOME is the PVC, so a stale route from an older image must be replaced
    rather than found and kept."""
    harness.prepare()
    harness.route_patch_path().write_text("- id: acp\n  config: {provider: stale}\n")

    harness.prepare()

    rows = {row["id"]: row for row in _patch_rows(harness.route_patch_path())}
    assert rows["acp"]["config"]["provider"] == harness.PROVIDER


def test_a_profile_pulled_from_git_keeps_its_own_patch_file():
    """The pulled profile's patch layer is Noah's - skills, plugins, tweaks.
    `prepare` must not truncate it to make room for routing, which is the
    bug this whole file-placement decision exists to prevent."""
    home = harness.prepare()
    mine = home / "profiles" / "acp" / "cordis.patch.yml"
    mine.write_text("- id: system-prompt\n  config: {persona: pulled from git}\n")

    harness.prepare()

    assert "pulled from git" in mine.read_text()


def _snapshot_repo(tmp_path, files: dict, extra: dict | None = None):
    """A real git repository in `harness-sync`'s own format: one JSON
    document holding each configuration file as verbatim text. Real, not
    mocked - the failure this pull has to survive is a repository that is
    shaped slightly differently than expected, and a mock asserts only that
    we called git."""
    import subprocess

    origin = tmp_path / "origin"
    (origin / "config").mkdir(parents=True)
    (origin / "config" / "harness-config.json").write_text(
        json.dumps({"schema": 1, "version": 1, "files": files})
    )
    for relative, text in (extra or {}).items():
        target = origin / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    run = lambda *a: subprocess.run(a, cwd=origin, check=True, capture_output=True)
    run("git", "init", "--initial-branch=main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    run("git", "add", "-A")
    run("git", "commit", "-m", "snapshot")
    return origin


def test_the_pulled_profile_lands_in_the_harness_home(tmp_path, monkeypatch):
    """EVE-24: "It should pull my profile from git"."""
    origin = _snapshot_repo(
        tmp_path, {"settings.yaml": "permission:\n  defaultPreset: danger-full-access\n"}
    )
    monkeypatch.setenv("EVE_COMPUTER_DSH_PROFILE_REPO", str(origin))
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()

    home = harness.prepare()

    assert "danger-full-access" in (home / "settings.yaml").read_text()


def test_the_route_outlives_a_snapshot_that_names_it(tmp_path, monkeypatch):
    """The pull is not trusted to leave the routing alone. `harness-sync`
    snapshots whatever is in the harness home, so a laptop that once ran
    this code would push a route file of its own - pointing at a proxy
    reachable from a living room, not from the cluster."""
    origin = _snapshot_repo(
        tmp_path, {harness.route_patch_path().name: "- id: acp\n  config: {provider: laptop}\n"}
    )
    monkeypatch.setenv("EVE_COMPUTER_DSH_PROFILE_REPO", str(origin))
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()

    harness.prepare()

    rows = {row["id"]: row for row in _patch_rows(harness.route_patch_path())}
    assert rows["acp"]["config"]["provider"] == harness.PROVIDER


def test_a_snapshot_cannot_write_outside_the_harness_home(tmp_path, monkeypatch):
    """The repository is data this box did not author on this run. A path
    that climbs out of the home is the one thing in that data that could
    reach the rest of the filesystem, so it is dropped rather than
    followed."""
    origin = _snapshot_repo(tmp_path, {"../escaped.yaml": "nope\n"})
    monkeypatch.setenv("EVE_COMPUTER_DSH_PROFILE_REPO", str(origin))
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()

    home = harness.prepare()

    assert not (home.parent / "escaped.yaml").exists()


def test_an_unreachable_repository_still_yields_a_bootable_home(tmp_path, monkeypatch):
    """Same argument as bootstrap.sh's package replay: the self-heal must
    not become the outage. What a failed pull costs is Noah's preferences,
    not the agent."""
    monkeypatch.setenv("EVE_COMPUTER_DSH_PROFILE_REPO", str(tmp_path / "does-not-exist"))
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()

    home = harness.prepare()

    assert (home / "profiles" / "acp" / "package.json").exists()
    rows = {row["id"]: row for row in _patch_rows(harness.route_patch_path())}
    assert rows["acp"]["config"]["provider"] == harness.PROVIDER
