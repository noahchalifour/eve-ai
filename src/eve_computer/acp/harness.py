"""The DeepSeek harness's home directory: the profile it boots, the route
it serves, and the git pull that fills in everything else.

`dsh` is the only one of the four agents with no command-line model flag
and no config file of its own design. It boots a PROFILE - a directory of
composed plugin layers under `$DSH_HOME/profiles/<name>` - and the model is
one row of that composition. Which makes this file the `codex-config.toml`
equivalent for the fourth agent, and it is a module rather than a template
for one reason: the other two templates are static text with a base URL
substituted, and this one has to survive a directory that something else
also writes.

WHY THE ROUTE IS NOT IN THE PROFILE. EVE-24 asks for two things: the
harness pulls Noah's profile from git, and it is the default. The profile
his laptop pushes carries a `settings.yaml`, a home-level
`cordis.patch.yml`, and `profiles/acp/*` - his plugins, his skills, his
preferences. It also carries HIS model routing, which names providers this
box has no key for. If this box wrote its routing into any of those paths,
the next pull would overwrite it and every session would fail at its first
prompt; if it wrote them after the pull, it would clobber his profile
instead. So the route goes in a file at the harness home root that only
this box writes, passed as `dsh --patch` - the LAST layer the launcher
applies, after every bundle, after the profile's patch file, and after the
home-level one. Verified against a real `dsh` in tests/test_acp_dsh_live.py,
including the case where a pulled patch file tries to claim the same row.

WHY THE MODEL IS AN EXPRESSION. `!!js process.env.X` is evaluated by the
launcher when the entry activates, so one patch file serves every model Eve
names - the model arrives as environment on the subprocess the registry
spawns, which is as close to `codex-acp --model <name>` as a config-only
harness gets. It has to appear TWICE: once as the ACP row's model, and once
as the route's model list, because pi-ai refuses to serve a model its route
does not declare.

WHY THE PULL IS BEST-EFFORT. Same argument as bootstrap.sh's package
replay: a repository that is unreachable, private to a token this pod does
not have, or simply not configured must not crash-loop the box. The stock
profile boots without it; what is lost is Noah's preferences, not the
agent.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

import yaml

from eve_computer.settings import get_computer_settings

logger = logging.getLogger(__name__)

# The pi-ai route name. Not "litellm" by accident: pi-ai refuses to store a
# credential under a key outside its lowercase-hyphen grammar, and a route
# named for the proxy is what makes `no adapter registered for provider
# "litellm"` readable when someone gets the spelling wrong.
PROVIDER = "litellm"

# Read by the launcher at entry activation, not by this process. `dsh` takes
# no --model flag, so this pair is the whole per-session interface.
MODEL_VAR = "EVE_ACP_MODEL"
API_KEY_VAR = "LITELLM_API_KEY"

_PROFILE = "acp"
_ROUTE_FILE = "eve-route.patch.yml"

_MANIFEST = {
    "name": "dsh-profile-acp",
    "private": True,
    "dependencies": {},
    "dsh": {
        "profile": {
            "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-acp-app"],
            # `startup`, not `live`: this box never edits a patch file while a
            # session is running, and a watcher over a PVC is a file handle
            # with no reader.
            "patchReload": "startup",
        }
    },
}


def home_path() -> Path:
    return Path(get_computer_settings().dsh_home)


def route_patch_path() -> Path:
    """The route, at the harness home root - deliberately NOT under
    `profiles/`, which is what `harness-sync pull` overwrites."""
    return home_path() / _ROUTE_FILE


def _profile_dir() -> Path:
    return home_path() / "profiles" / _PROFILE


def _route_rows() -> list[dict]:
    settings = get_computer_settings()
    # `/v1`: the proxy speaks the OpenAI completions shape there, and pi-ai
    # appends only the path within the API, never the version segment.
    base_url = f"{settings.litellm_base_url.rstrip('/')}/v1"
    model = _expression(f"process.env.{MODEL_VAR}")
    return [
        {
            "id": "llm-pi-ai",
            "config": {
                "providers": {
                    PROVIDER: {
                        "displayName": "LiteLLM",
                        "api": "openai-completions",
                        "baseURL": base_url,
                        # A reference, resolved per request. The key itself
                        # never reaches this file - it lands on the PVC, and
                        # `harness-sync push` reads what is on the PVC.
                        "apiKeyEnv": API_KEY_VAR,
                        "models": [{"id": model, "name": model}],
                    }
                }
            },
        },
        {
            "id": "acp",
            "config": {"provider": PROVIDER, "model": model},
        },
    ]


# `!!js` is the shorthand; this is what it expands to, and what a YAML
# emitter has to be given to produce the shorthand back.
EXPRESSION_TAG = "tag:yaml.org,2002:js"


class _Expression(str):
    """A launcher `!!js` scalar. A plain string would be a literal model
    name; this is what makes the model per-session."""


def _expression(source: str) -> _Expression:
    return _Expression(f"!!js {source}")


def _represent_expression(dumper, data):
    return dumper.represent_scalar(EXPRESSION_TAG, str(data)[len("!!js ") :])


def _construct_expression(loader, node):
    return _expression(loader.construct_scalar(node))


_Dumper = type("_Dumper", (yaml.SafeDumper,), {})
_Dumper.add_representer(_Expression, _represent_expression)

Loader = type("Loader", (yaml.SafeLoader,), {})
Loader.add_constructor(EXPRESSION_TAG, _construct_expression)


def _pull_profile(home: Path) -> None:
    """Noah's configuration, from the repository his laptop pushes to.

    Cloned into a scratch directory and copied in, rather than cloned over
    the home: the home already holds `sessions/` and the route this box
    just wrote, and git refuses to clone into a directory with content in
    it. Nothing here is fatal - see the module docstring.
    """
    settings = get_computer_settings()
    if not settings.dsh_profile_repo:
        return
    scratch = home / ".profile-pull"
    try:
        shutil.rmtree(scratch, ignore_errors=True)
        subprocess.run(
            [
                "git", "clone", "--depth", "1",
                "--branch", settings.dsh_profile_branch,
                settings.dsh_profile_repo, str(scratch),
            ],
            check=True, capture_output=True, timeout=120,
        )
    except (subprocess.SubprocessError, OSError):
        logger.warning("could not pull the dsh profile; booting the stock one", exc_info=True)
        shutil.rmtree(scratch, ignore_errors=True)
        return

    try:
        _restore_snapshot(scratch, home)
    except (OSError, ValueError, json.JSONDecodeError):
        logger.warning("the pulled dsh profile could not be applied", exc_info=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _restore_snapshot(scratch: Path, home: Path) -> None:
    """`harness-sync`'s own format: one JSON document holding each
    configuration file as verbatim text, keyed by its path under the harness
    home. Read rather than copied wholesale so a repository that also holds
    session transcripts and clone scripts contributes only its config."""
    snapshot = scratch / "config" / "harness-config.json"
    if not snapshot.exists():
        logger.warning("the pulled repository holds no harness-config.json")
        return
    files = json.loads(snapshot.read_text()).get("files", {})
    for relative, text in files.items():
        if not isinstance(text, str):
            continue
        target = (home / relative).resolve()
        # A snapshot is data, not an instruction to write outside the home.
        if home.resolve() not in target.parents:
            logger.warning("the pulled profile named %s, outside the home", relative)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


def prepare() -> Path:
    """The harness home, ready to boot. Called on every container start, for
    the same reason bootstrap.sh rewrites the other two agents' config: $HOME
    is the PVC, so a route from an older image would otherwise outlive it."""
    home = home_path()
    profile = _profile_dir()
    profile.mkdir(parents=True, exist_ok=True)

    # The pull runs first so the route is written after it and cannot be
    # overwritten by a snapshot that names the same path.
    _pull_profile(home)

    manifest = profile / "package.json"
    if not manifest.exists():
        manifest.write_text(json.dumps(_MANIFEST, indent=2) + "\n")
    patch = profile / "cordis.patch.yml"
    if not patch.exists():
        patch.write_text("[]\n")

    route_patch_path().write_text(
        yaml.dump(_route_rows(), Dumper=_Dumper, sort_keys=False, default_flow_style=False)
    )
    return home
