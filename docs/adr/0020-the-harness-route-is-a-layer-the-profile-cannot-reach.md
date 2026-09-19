# 20. The harness route is a layer the pulled profile cannot reach

**Status:** Accepted
**Date:** 2026-09-18

## Context

EVE-24 adds the DeepSeek harness as a fourth ACP agent, and asks for two
things: it should pull Noah's profile from git, and it should be the
default.

The first three agents took a line of configuration each, which is the
property ADR 0016's registry was built to preserve. `dsh` does not fit that
shape for one reason: **it has no model flag.** Claude Code reads
`ANTHROPIC_MODEL` from its environment; Codex and OpenCode take
`--model <name>` on the command line. `dsh` boots a *profile* - an ordered
stack of plugin-bundle patch layers under `$DSH_HOME/profiles/<name>` - and
the model is one row of that composition. There is no argument to pass.

That alone would be a fourth config template beside `codex-config.toml` and
`opencode.json`. What makes it a decision is the git pull. The repository
`dsh harness-sync push` writes holds a `settings.yaml`, a home-level
`cordis.patch.yml`, and `profiles/acp/*` - and it holds Noah's model
routing, naming providers this box has no key for and endpoints reachable
from a living room rather than from the cluster. The pull and the routing
want the same files.

Getting that wrong is silent in the direction that matters. A profile whose
routing survived the pull leaves every session failing at its first prompt
with `no adapter registered for provider`, in production, on a box with no
interactive user.

## Decision

**This box's routing lives in a file only this box writes, applied as the
last layer.**

`eve_computer/acp/harness.py` writes `$DSH_HOME/eve-route.patch.yml` -
deliberately at the harness home root, outside `profiles/`, which is what a
pull overwrites - and `registry.py` passes it as `dsh --patch <file>`. The
launcher applies `--patch` after every bundle, after the profile's own patch
file, and after the home-level one. So the pulled profile keeps everything
it is for (skills, plugins, preferences, persona) and cannot reach the two
rows that decide which proxy this box talks to.

The model is `!!js process.env.EVE_ACP_MODEL`, evaluated by the launcher at
entry activation, so one file serves every model Eve names - which is as
close to `codex-acp --model <name>` as a config-only harness gets. It
appears twice, because pi-ai refuses to serve a model its route does not
declare.

`prepare()` runs the pull first and writes the route after it, so a snapshot
that names `eve-route.patch.yml` - which a laptop that once ran this code
would produce - loses to the file written second.

**The harness becomes the tiebreak agent,** replacing Codex (ADR 0004). It
reaches LiteLLM through the same proxy as the other three, so the
zero-metered-spend property that made Codex the tiebreak is unchanged; what
it adds is that the untargeted case runs the profile Noah actually works in.

## Consequences

**Node is pinned at the image, not discovered on the box.** Debian trixie
ships Node 20, on which `dsh` installs cleanly and then exits 0 printing
nothing. Not a crash - a launcher that never boots. `Dockerfile.eve-computer`
installs Node 22 from NodeSource because a silent failure is the mode this
repository is least equipped to notice.

**`additionalDirectories` is now a preference, not a requirement.** The
harness refuses that optional ACP v1 field outright, so `session.py` retries
without it on `-32602`. It costs nothing: every worktree is created under
the session directory that is already the cwd. Failing a session over a
field the protocol calls optional would be this box choosing which compliant
agents it will talk to.

**A live tier test exists for the layering itself.** Three ways this could
break are invisible to a unit test: a `!!js` scalar emitted as a plain
string boots fine and runs against a model named
`process.env.EVE_ACP_MODEL`; a route declaring the model once still answers
`initialize`; and layer precedence is a property of the launcher, not of us.
`tests/test_acp_dsh_live.py` drives the real binary and asserts the
*resolved* model, including with a hijacking patch file planted in both
positions a pull could reach.

**A deployment with no profile repository loses nothing but the
preferences.** `dsh_profile_repo` is empty by default and the pull is
best-effort, on the same argument as bootstrap.sh's package replay: the
self-heal must not become the outage.
