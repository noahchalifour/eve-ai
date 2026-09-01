# Is Eve in sync? Short answer: the cluster is, the repo isn't.

Read-only check across all three layers. Manifests ↔ cluster are perfectly in sync. Repo ↔ manifests has a large gap: **four merged PRs' worth of work (Phases 5a, 5b, 5c and the model fallback) sit on `main` untagged, so no images were ever built and nothing of it is deployed.**

## Layer 1: manifests ↔ cluster — in sync

ArgoCD reconciles all three Eve apps from `noahchalifour/home-lab-infrastructure`, `targetRevision: HEAD`:

| App | Sync | Health | Synced revision |
|---|---|---|---|
| `eve` | Synced | Healthy | `dec36f0` |
| `eve-tools` | Synced | Healthy | `dec36f0` |
| `eve-ambient` | Synced | Healthy | `dec36f0` |

`dec36f0` is the current tip of the infra repo's `origin/main`, so the cluster is running exactly what's committed.

Pinned tags vs. what is actually running — identical, no drift:

| Workload | Manifest pin | Running image | Pod age / restarts |
|---|---|---|---|
| `eve` (ns `eve`) | `v0.2.4` | `ghcr.io/noahchalifour/eve-ai:v0.2.4` | 30h / 0 |
| `eve-tools` (ns `eve-tools`) | `v0.2.4` | `ghcr.io/noahchalifour/eve-tools:v0.2.4` | 30h / 0 |
| `eve-ambient` (ns `eve`) | `v0.2.0` | `ghcr.io/noahchalifour/eve-ambient:v0.2.0` | 3d7h / 0 |

Supporting infra is healthy too: `eve-db-1` / `eve-db-2` (CNPG vectorchord 16.9) up 10d, `eve-redis-0` up 10d, zero restarts anywhere, no warning events in either namespace. `EVE_ENV=production`, `EVE_AUTH_MODE=oidc`, `EVE_AMBIENT_ENABLED=true` — ambient is live, not dark.

## Layer 2: repo ↔ manifests — significantly out of sync

The last release tag is **`v0.2.4`** (`9ab31c4`). Since then `origin/main` has advanced **66 commits / 4 merged PRs**, none of it tagged, so `.github/workflows/build.yml` (publish is tag-only) never built images for any of it:

- **PR #16 — Phase 5a, self-improvement** (Eve authors her own rules/procedures)
- **PR #17 — Phase 5b, eval harness** (`eve-eval` CLI, A/B, regression gate)
- **PR #18 — model fallback (EVE-2)**, `anthropic/claude-sonnet-5` as the tool-capable LiteLLM fallback
- **PR #19 — Phase 5c, sandboxed tools** (`propose_tool`, approval interrupt, the new `eve-sandbox` service, Alembic migration move)

Three things worth flagging inside that gap:

1. **The model fallback is half-shipped.** The *infrastructure* side landed and is deployed (`dec36f0`, "Wire anthropic/claude-sonnet-5 as litellm's tool-capable fallback (eve-ai EVE-2)"), but the *eve-ai* side (`95120de`) is untagged and not running. LiteLLM is configured for a fallback that the deployed Eve build doesn't know about.
2. **Phase 5c adds a fourth image and service, `eve-sandbox`, that has nowhere to go.** `build.yml` on `main` now has `eve-sandbox` in the publish matrix, but there is no `kubernetes/apps/eve-sandbox/` in the infra repo and no `eve-sandbox` package in GHCR. Releasing 5c requires writing new manifests + an app-of-apps entry first — it is not just a tag bump.
3. **Phase 5c moves migrations to Alembic** (`5222307`, plus `f8231f6` copying alembic files into the production image). That's a schema-migration change against the live `eve-db` cluster, so the next release is not a routine rollout.

Also a minor, low-risk pin drift: **`eve-ambient` is pinned `v0.2.0` while the other two are `v0.2.4`.** I checked — `src/eve_ambient/` and `Dockerfile.eve-ambient` had zero changes between those tags, and only one shared module it imports changed (`3bbc6fd`, PAT minting). So it's cosmetic rather than a real behavioural lag, but it's worth aligning at the next release so all three aren't on split tags.

## Layer 3: your local checkouts — both stale, and eve-ai has diverged

**`~/GitHub/eve-ai` — local `main` has diverged from `origin/main`.**
- Common ancestor: `a369d33` ("Add scripts/chat.py")
- Your local `main` = `a369d33` + **1 unpushed commit**: `06d4985` *"fix(memory): tag background REFLEX calls nostream so they don't leak into the client stream"*. I confirmed via `git ls-remote` that this commit is on no remote branch — it exists only on your machine.
- `origin/main` = `a369d33` + **22 commits** you don't have locally (all of Phase 5c and the model fallback).

So `git describe` reporting `v0.2.4-43-g06d4985` is misleading — you're not 43 commits ahead of the release, you're on a stale side branch. A `git pull --rebase` is needed before anything else.

Also uncommitted/untracked locally: `.claude/` and `docs/superpowers/plans/2026-08-28-background-memory-extraction.md`.

**`~/GitHub/home/lab/infrastructure` — 1 commit behind `origin/main`.** Missing `dec36f0` (the LiteLLM fallback commit). I diffed `kubernetes/apps/eve*` between your checkout and `dec36f0` — **empty**, so your local Eve manifests are accurate; the missed commit touched the `litellm` app only. Working tree is dirty but it's all `graphify-out/` artifacts, no manifest changes.

## Bottom line

- **Running system: healthy and exactly matching its manifests.** Nothing is broken and nothing needs urgent attention.
- **The gap is release, not drift:** four phases of merged work have never been tagged, so they've never been built or deployed. Eve in the cluster is the `v0.2.4` feature set.
- **Before any of it ships**, three things need doing: pull/rebase your diverged local `main` (and push or drop `06d4985`), author `eve-sandbox` manifests + app-of-apps entry, and plan for the Alembic migration cutover against the live DB.
- CI is green across the last 12 runs and there are no open PRs, so the branch is in a releasable state whenever you want to cut `v0.3.0`.
