# Is Eve fully in sync?

**No.** `check-sync.sh` exits 1. Read-only check — I changed nothing.

```
WHERE                VERSION
pyproject.toml       v0.1.0
latest git tag       v0.2.4
overlay eve          v0.2.4
overlay eve-tools    v0.2.4
overlay eve-ambient  v0.2.0     <-- drift
running eve          v0.2.4
running eve-tools    v0.2.4
running eve-ambient  v0.2.0     <-- drift

OUT OF SYNC
```

There are three separate things going on, and only one of them is a cluster problem.

## 1. eve-ambient is four releases behind (the real drift)

`eve-ambient` is pinned *and* running `v0.2.0` while `eve` and `eve-tools` are on
`v0.2.4`. It missed v0.2.1 → v0.2.4, seven commits.

This is a caller/library skew, not just a stale label. eve-ambient imports Eve's
`settings`, `family`, `models`, `memory` and `specialists.permissions` out of the
same `src/eve`. Between v0.2.0 and v0.2.4 those shared paths changed:

- `src/eve/memory/db.py` — added migration `0004_pat` (new `eve_pat` table)
- `src/eve/auth.py`, new `src/eve/pat.py` — personal access tokens
- `src/eve_tools/monarch.py` + settings — Monarch domain move, session token, TOTP/MFA

So the ambient pod is running the v0.2.0 copy of the memory module against a
database the v0.2.4 `eve` pod has already migrated forward. Nothing is visibly
broken right now (the added table is one ambient doesn't read), but that is luck,
not design — this is exactly the state the release skill exists to prevent, and
it has persisted across four releases.

## 2. The GitOps half is internally consistent — nothing is stuck

Worth saying plainly, because "out of sync" can sound like a failed rollout. It
is not:

- All three ArgoCD apps: `Synced` / `Healthy`, all at infra revision `dec36f0`
- Running images match the overlay pins exactly, all three
- `eve`'s `eve-migrate` initContainer is `v0.2.4`, matching its main container
- All pods `Running`, `0` restarts (`eve` 30h, `eve-ambient` 3d7h, `eve-tools` 30h)

The cluster is faithfully running what the manifests ask for. The manifests are
what's wrong.

## 3. `pyproject.toml` still says 0.1.0

Known, long-standing, cosmetic — the version file was never wired to the tags.
It costs nothing to fix on the next release and it silences a permanently-red row
in the check.

## The bigger thing: 66 unreleased commits, and a fourth image

`origin/main` is **66 commits ahead of `v0.2.4`**. Everything from phases 5b and
5c is built, merged, green in CI — and entirely undeployed. That includes two
things that make the next release more than a three-way version bump:

- **Migrations moved to Alembic** (`5222307`, `f8231f6`). The first deploy past
  v0.2.4 changes how the `eve-migrate` initContainer works. That is the riskiest
  step in the queue and it is worth its own attention, not a routine patch bump.
- **A fourth image.** `Dockerfile.eve-sandbox` exists on `origin/main` and
  `build.yml`'s publish matrix now has four entries, not three. There is **no
  `eve-sandbox` app in `kubernetes/apps/`** and nothing named sandbox is running
  in the cluster. The invariant this skill enforces is written for three images
  and eight pins; the next release makes it four images and needs a new infra app
  before the sandbox tooling does anything on the cluster.

## Two housekeeping problems that would bite a release cut today

- **Your local eve-ai checkout has diverged from `origin/main`.** `main` here is
  at `06d4985`, which is **1 ahead / 24 behind** origin, forking at `a369d33`.
  The local tree doesn't even contain `Dockerfile.eve-sandbox`. Tagging from this
  working copy would publish the wrong tree. There's also one unpushed commit
  (`06d4985`, the background-REFLEX nostream fix) that exists nowhere else.
- **The infra repo is dirty** — a pile of modified and untracked `graphify-out/`
  artifacts. Those would ride along into a release PR. It's also 1 behind origin
  (`dec36f0`, a litellm change that doesn't touch Eve).

## What would actually fix it

Not for me to run — flagging the shape:

1. Reconcile local `main` with `origin/main` and get `06d4985` pushed (or dropped).
2. Clean or gitignore `graphify-out/` in the infra repo.
3. Decide the version. 66 commits with new env/config surface, a new service and a
   migration-system change is a **minor**, `v0.3.0`, not a patch.
4. Add the `eve-sandbox` infra app before or with that release, and extend
   `check-sync.sh` to a fourth pin — otherwise the same drift reappears with a
   new name.
5. Cut it across all three (four) overlays in one PR. eve-ambient jumping
   v0.2.0 → v0.3.0 belongs in the PR body: that component carries far more change
   than the diff suggests.

The Alembic switch is the part I'd want eyes on during rollout — a failure in the
`eve-migrate` initContainer is a real migration failure, and a partially applied
schema is not fixed by rolling the image back.
