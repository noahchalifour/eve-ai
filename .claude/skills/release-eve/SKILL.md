---
name: release-eve
description: Ship a new version of Eve to Noah's home lab Kubernetes cluster — bump the version, tag eve-ai, confirm every image published to GHCR, bump all the image pins in home-lab-infrastructure in one PR, and verify the ArgoCD rollout. Use this whenever the user wants to release, deploy, ship, cut a version of, roll out, or push Eve to the lab/cluster/prod, or asks to bump Eve's version or get a fix "onto the cluster" — even if they only name one component (eve, eve-tools, eve-ambient, eve-sandbox), because every image built from the repo must move to the same version together. Also use to diagnose or repair version drift or skew between Eve's images, or to check what version is actually running.
---

# Releasing Eve

## Why this exists

Eve is one codebase that builds **several** images from **one** commit.

**The publish matrix in `.github/workflows/build.yml` is the list — read it,
never assume a count.** It was three images for most of Eve's life and is four
on `main` today (`eve-sandbox` arrived with Phase 5c). Anything that hardcodes
the set silently stops covering the newest image, which is the same failure as
the drift below wearing a different hat.

The mapping is mechanical, with one asymmetry: image `eve-ai` deploys as app
`eve` (Deployment `eve` in namespace `eve`, plus its `eve-migrate`
initContainer on the same tag); every other image `X` deploys as app `X` at
`kubernetes/apps/X/overlays/homelab/`.

They share `src/eve`: `eve-ambient` imports Eve's `settings`, `family`,
`models`, `memory`, and `specialists.permissions`; both `eve-ambient` and the
specialists call `eve-tools` over HTTP. A version skew between them is a skew
between a caller and its own library — schema changes, permission changes and
migrations land in whichever image ships first and the others deserialize
against an older contract.

Worse, the skew is usually *quiet*. Only `eve` runs migrations (via its
initContainer), so the database advances with `eve`'s version while a lagging
`eve-ambient` keeps writing the old shape — you get missing rows, not a
crash. Do not read "everything is Healthy" as "everything is fine".

That is not hypothetical. Each image is pinned in its own kustomize overlay,
releases were historically cut one image at a time (PRs #138, #139, #140), and
`eve-ambient` sat on `v0.2.0` through four releases of the other two.
**The single most important thing this skill does is make them all move as
one.** Everything else is mechanics.

## The invariant

One version string, pinned once per image per layer: `pyproject.toml` · the
git tag · each app's `overlays/homelab/kustomization.yaml` `newTag` · each
running Deployment. They must all agree.

`check-sync.sh` (bundled here) derives the image list from the build matrix,
reads every pin, and exits non-zero if they disagree. It also flags an image
that CI builds but no overlay deploys — `eve-sandbox` is in that state now,
and an image nobody is watching is how drift starts.

Run it at the start of a release, after the manifest edit, and after the
rollout. Trust it over your own reading of a diff: it is the only thing in
either repo that couples the two.

```bash
.claude/skills/release-eve/check-sync.sh            # do the pins agree?
.claude/skills/release-eve/check-sync.sh v0.2.5     # do they all equal this?
```

Repo locations, override with `EVE_REPO` / `EVE_INFRA_REPO` if a worktree
or a different clone is in play:

- eve-ai — this repo
- infrastructure — `~/GitHub/home/lab/infrastructure`
  (`noahchalifour/home-lab-infrastructure`)

## Before you start

Run `check-sync.sh` with no argument. If it reports drift *before* you begin,
say so and note that this release will heal it — the drifted component jumps
several versions at once, which belongs in the PR body because it means the
release carries more change than its own diff suggests.

Then confirm the ground is stable:

```bash
git fetch -q origin
git status --porcelain
git rev-list --left-right --count main...origin/main   # want 0  0
git -C ~/GitHub/home/lab/infrastructure fetch -q && \
  git -C ~/GitHub/home/lab/infrastructure status --porcelain
```

A dirty tree in either repo stops the release: uncommitted work in eve-ai will
not be in the image, and uncommitted work in infrastructure rides along in
your PR.

Check the divergence count in **both** directions and insist on `0 0`. Behind
is the dangerous one and the easy one to miss — CI builds the tagged commit,
so tagging a checkout that is behind publishes a tree missing everything
merged since, under a version number claiming otherwise. Ahead
means a local commit exists nowhere else; push it or drop it before tagging,
don't ship around it.

## Choosing the version

Look at what actually shipped, then propose:

```bash
git log $(git tag --sort=-v:refname | head -1)..main --oneline
```

Semver against the *last released tag*, and say which bucket you picked and
why in one line — patch for fixes and dependency pins, minor for new
capability, tables, or config surface. New env vars, a new migration, or a new
tool are minor: they change what the deployment needs from its environment.
Ask the user to confirm before tagging. A tag is the one step here that is
awkward to take back, because the GHCR build fires on it.

## 1. Cut the tag in eve-ai

`pyproject.toml`'s version tracks the release (it was allowed to drift to
`0.1.0` while tags reached `v0.2.4`; do not reintroduce that). Note the tag
carries the `v`, the pyproject value does not.

```bash
sed -i '' 's/^version = ".*"/version = "0.2.5"/' pyproject.toml
uv lock                       # <- not optional, see below
uv run pytest -m "not integration and not live" -q
git commit pyproject.toml uv.lock -m "Release v0.2.5" && git push origin main
git tag v0.2.5 && git push origin v0.2.5
```

**`uv lock` is the step that will bite you.** `uv.lock` records the `eve`
package's own version, so bumping `pyproject.toml` alone makes the lockfile
stale; CI's first job runs `uv sync --frozen`, which hard-fails on a stale
lock, and that job gates the image build. The result is a published tag with
no images behind it — and since a published tag must never move, recovering
costs a whole extra version. `uv lock --check` tells you where you stand.

`main` is unprotected, so a direct push is fine and matches existing history.
Run the unit tier before tagging rather than after — the tag is what triggers
publishing, and CI runs the same tier, so catching a failure locally saves a
build and a dead tag.

## 2. Confirm every image published

`.github/workflows/build.yml` publishes on `v*` tags only, as a matrix behind
the test jobs. Watch it:

```bash
gh run list --workflow build.yml --limit 5 \
  --json databaseId,headBranch,event,status,conclusion
gh run watch <databaseId> --exit-status
```

Then verify against the registry itself, which is the actual gate:

```bash
.claude/skills/release-eve/images-published.sh v0.2.5
```

A green run is good evidence but not proof — legs get skipped, reruns go
green, and a partially published release is precisely the state that produces
drift one layer down. The script asks GHCR whether each image in the matrix
really carries the tag. It needs no auth: GHCR issues anonymous pull tokens
for these packages. (`gh api .../packages` does *not* work — the local token
lacks `read:packages` — so a failure there tells you nothing about the build,
and it is an easy wrong conclusion to draw.)

If a leg goes red, stop: the tag is published but its image is not. Fix
forward on `main` and cut the next patch tag. **Never move or delete a
published tag** — the cluster and GHCR would disagree about what a version
means, and nothing in the lab would detect it.

## 3. Bump every pin in one PR

One branch, one PR, every overlay. Expand the brace list below to whatever
the build matrix actually holds — and if the matrix has an image with no
overlay (`eve-sandbox` today), that image needs an infra app created before it
can be part of a release; say so rather than quietly leaving it out. The
unified branch name is itself part of the fix: `eve-v0.2.4` / `eve-tools-v0.2.4`
as separate branches is the habit that produced the drift.

```bash
cd ~/GitHub/home/lab/infrastructure
git checkout main && git pull && git checkout -b eve-release-v0.2.5
for f in kubernetes/apps/{eve,eve-tools,eve-ambient}/overlays/homelab/kustomization.yaml; do
  sed -i '' 's/newTag: .*/newTag: v0.2.5/' "$f"
done
EVE_INFRA_REPO=$PWD EVE_SKIP_LIVE=1 \
  ~/GitHub/eve-ai/.claude/skills/release-eve/check-sync.sh v0.2.5
```

`EVE_SKIP_LIVE=1` because the pods are still legitimately on the old tag here
— nothing has synced yet. This must pass green before you commit; if it does
not, an overlay was missed and you are about to ship the exact drift this
skill exists to prevent.

Write the PR body from the real log, `git log v0.2.4..v0.2.5 --oneline` in
eve-ai — the infra diff is a set of identical one-line changes and says nothing
about what is being shipped. Lead with the user-visible change, and call out
anything operationally load-bearing: a new migration, a new required env var
or secret key, or a component skipping several versions at once.

Commit explicit paths, not `-am`. That repo carries `graphify-out/` artifacts
that are tracked and routinely dirty, and `-a` sweeps them into your release
PR — a diff that should be one line per app becomes unreviewable.

That repo's `CLAUDE.md` requires docs and Gatus entries to move with the
change. A version bump adds no service and no `*.chalifour.dev` host, so
neither needs an edit here — but if this release introduces a new externally
facing surface, that rule applies and the Gatus entry belongs in this PR.

```bash
git commit kubernetes/apps -m "eve to v0.2.5: <what shipped>"
git push -u origin eve-release-v0.2.5
gh pr create --title "eve to v0.2.5: <what shipped>" --body "<the release notes>"
gh pr merge --squash   # once checks pass
```

ArgoCD tracks `HEAD` of `main` with `automated` sync and `selfHeal`, so the
merge *is* the deploy. Nothing further is triggered by hand.

## 4. Verify the rollout

Sync is not the same as healthy, and healthy is not the same as running the
version you shipped — a pod that cannot pull the image can report `Synced`
while the old ReplicaSet keeps serving. Check the images:

```bash
kubectl -n argocd get applications.argoproj.io eve eve-tools eve-ambient
kubectl -n eve rollout status deploy/eve deploy/eve-ambient --timeout=5m
kubectl -n eve-tools rollout status deploy/eve-tools --timeout=5m
~/GitHub/eve-ai/.claude/skills/release-eve/check-sync.sh v0.2.5
```

(App names above are today's set; `check-sync.sh` prints the current one —
use that rather than this list if they disagree.)

`check-sync.sh` passing on every row is the definition of done. Report that,
not a summary of the steps.

Expect a short gap, not a seamless rollout: these Deployments are `replicas: 1`
with `strategy: Recreate` (pinned because `aegra serve` migrates at startup),
so Eve is down for roughly 30-90s per app. Worth saying out loud if someone is
mid-conversation with her.

If ArgoCD has not picked the merge up within a couple of minutes,
`kubectl -n argocd patch app <name> --type merge -p '{"operation":{"sync":{}}}'`
forces it. The `argocd` CLI is installed but its session token is usually
expired, so reach for `kubectl` first.

## Healing drift without cutting a release

If `check-sync.sh` shows laggards but a **common tag already exists for every
image** — the usual case, since one workflow run publishes them all — you do
not need a new version. Confirm the images are there, then raise the laggards
to that tag in a single infra PR and stop:

```bash
.claude/skills/release-eve/images-published.sh v0.2.4
```

This is strictly cheaper and lower-risk than a fresh release, because it ships
no new code — so prefer it, and keep it as its own PR. Do not fold a drift
heal into a feature release: the heal wants to be revertible on its own, and a
laggard jumping several versions at once is already enough change to reason
about.

`pyproject.toml` and `uv.lock` are the exception — they are not images and no
tag fixes them, so they need their own commit in eve-ai to clear those rows.

## When it goes wrong

A rollback is a forward change to the pins, never a moved tag: set every
overlay back to the previous version in one PR and merge. `selfHeal` means
editing a Deployment with `kubectl` gets reverted within the minute, so live
edits are only ever a diagnostic, never a fix.

`eve` CrashLooping right after a deploy is more often authentik than Eve —
Eve refuses to start unless `EVE_AUTH_MODE=oidc` has all three `EVE_OIDC_*`
values, so check the `ExternalSecret` and authentik's health before assuming
the image is bad. A failure in the `eve-migrate` initContainer is a real
migration failure; read its logs before rolling back, because a partially
applied schema may not be fixed by returning to the old image.

If only one component is broken, the temptation is to bump just that one. That
is exactly how the drift got there — every one of PRs #133, #134, #138, #139
and #140 was individually reasonable. Cut a new patch release across all
images, or heal to a common existing tag; never bump one alone.
