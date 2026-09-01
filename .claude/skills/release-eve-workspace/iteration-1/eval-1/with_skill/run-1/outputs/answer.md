# Deploying Eve to the lab — the plan (nothing executed)

I read the release skill and inspected the real state of both repos, GitHub Actions, and the cluster. Read-only. Here is what I found and what I'd do.

## Where things stand right now

`check-sync.sh` says **OUT OF SYNC** before we even start:

```
WHERE                VERSION
pyproject.toml       v0.1.0      <- drifted long ago, never tracked the tags
latest git tag       v0.2.4
overlay eve          v0.2.4
overlay eve-tools    v0.2.4
overlay eve-ambient  v0.2.0      <- four releases behind
running eve          v0.2.4
running eve-tools    v0.2.4
running eve-ambient  v0.2.0
```

Cluster is healthy — all three ArgoCD apps `Synced` / `Healthy`, all pods `Running`, no restarts. So this is a clean release, not a recovery.

The `eve-ambient` drift is the known one the skill calls out. This release heals it, which means ambient jumps `v0.2.0 -> v0.3.0` and picks up 5 commits touching `src/eve_ambient` **plus ~2,440 changed lines in the shared `src/eve`** it imports. That belongs in the PR body: the release carries more change for ambient than the infra diff suggests.

## Four things to settle before we touch anything

### 1. Which fix do you mean? Your local `main` has diverged

```
origin/main   77dea8c  Merge pull request #19 from noahchalifour/feat/eve-sandboxed-tools
local  main   06d4985  fix(memory): tag background REFLEX calls nostream ...
```

`git rev-list --left-right --count origin/main...HEAD` returns **24 behind, 1 ahead**. Your local commit `06d4985` (the nostream fix for `src/eve/memory/extract.py` — stops raw `{"operations": []}` JSON leaking into the client stream) is **not on `origin/main`** and is parented off `a369d33`, 24 commits back.

So "the fix I just merged" is ambiguous, and it changes the plan:

- **If you mean PR #19 (Phase 5c)** — it's on `origin/main`, CI is green, we can tag it. Your local nostream commit is a separate, unpushed thing that will *not* be in the image.
- **If you mean the nostream fix** — it isn't merged anywhere. It has to be rebased onto `origin/main` and pushed before we tag, or it silently ships nothing.

My read: you probably want both, and the cheapest path is to rebase the local commit onto `origin/main`, push, and release the combined tip. But I want you to confirm, because it's the one thing here I can't infer.

### 2. This is not a patch release — 66 commits, and a fourth image now exists

Since `v0.2.4`: **66 commits, 111 files, ~18,900 insertions.** Phases 5a (self-authoring), 5b (eval harness) and 5c (gated tool code) all landed. That includes:

- **Three Alembic migrations** (`0001_baseline`, `0002_eve_tool`, `0003_eve_tool_pending_dedup`) replacing the hand-rolled `MIGRATIONS` list.
- **A large new env surface** (`EVE_SELF_AUTHORING_ENABLED`, `EVE_EVAL_*`, `EVE_SANDBOX_*`, `EVE_LANGFUSE_HOST`) — all defaulted, all off by default, so no `ExternalSecret` change is needed.
- **A new fourth image: `ghcr.io/noahchalifour/eve-sandbox`.**

Version: **`v0.3.0`** — minor. New tables, new migrations, new config surface, a new service. Patch would be a lie about what's shipping.

### 3. The three-image invariant is now a four-image reality, and nothing tracks the fourth

`origin/main`'s `.github/workflows/build.yml` publishes a **four-way** matrix (`eve-ai`, `eve-tools`, `eve-ambient`, `eve-sandbox`). But:

- `infrastructure` has **no `eve-sandbox` app** — no Deployment, Service, NetworkPolicy, or Gatus check. `grep -rn eve-sandbox` across `kubernetes/` returns nothing.
- `check-sync.sh` only knows about three images. After this release it is an incomplete invariant checker.

The design spec (`docs/superpowers/specs/2026-08-27-eve-sandboxed-tools-design.md` §10.1) explicitly calls for that infra work: Deployment with `readOnlyRootFilesystem`, tmpfs `/tmp`, `automountServiceAccountToken: false`, a default-deny-egress NetworkPolicy, a Service, and a Gatus check on `/healthz`. None of it is done.

**Recommendation: ship v0.3.0 across the three deployed images now, and deploy `eve-sandbox` as a separate follow-up PR.** This is safe because `sandbox_enabled` defaults to `False` and `model_post_init` only demands `EVE_SANDBOX_API_KEY` when it's `true`. Eve will not try to reach a service that isn't there. The image gets built and published either way; it just sits in GHCR unused until you wire it up.

If you'd rather do it in one shot, say so — but then the infra PR grows a new app plus the `docs/architecture.md` §4 update and the Gatus entry that repo's `CLAUDE.md` requires, and it stops being a version bump.

### 4. Both working trees are dirty — infra's dirt is dangerous

- **eve-ai:** untracked `.claude/` and `docs/superpowers/plans/2026-08-28-background-memory-extraction.md`. Harmless — `git commit -am` won't pick up untracked files.
- **infrastructure:** local `main` is **1 commit behind** `origin/main` (`dec36f0`, the litellm sonnet-5 fallback wiring), and there are **modified tracked files under `graphify-out/`** plus a pile of untracked cache. `git commit -am` in that repo *will* sweep those into the release PR. This must be cleaned first — stash it, or gitignore `graphify-out/`, or commit it separately. This is exactly the failure mode the skill warns about.

---

## The plan

### Step 0 — Reconcile eve-ai's main and clean infra

```bash
cd ~/GitHub/eve-ai
git fetch origin
git rebase origin/main          # replays 06d4985 onto the 5c tip
uv run pytest -m "not integration and not live" -q
git push origin main
```

The rebase is where the nostream fix could conflict — `src/eve/memory/extract.py` was touched by the 5b/5c work. If it conflicts, resolve before going further; do not tag a tree you haven't rebuilt.

Then infra:

```bash
cd ~/GitHub/home/lab/infrastructure
git stash push -m "graphify-out" -- graphify-out   # or gitignore it
git checkout main && git pull
git status --porcelain                             # must be empty of tracked changes
```

### Step 1 — Cut `v0.3.0` in eve-ai

`pyproject.toml` version tracks the release and currently says `0.1.0`; this is where that drift gets fixed. Note the tag carries the `v`, pyproject does not.

```bash
cd ~/GitHub/eve-ai
sed -i '' 's/^version = ".*"/version = "0.3.0"/' pyproject.toml
uv run pytest -m "not integration and not live" -q
uv run pytest -m docker -q        # NEW: the image job now gates on this too
git commit -am "Release v0.3.0"
git push origin main
git tag v0.3.0 && git push origin v0.3.0
```

Two notes:

- Run the tests **before** the tag. The tag is what triggers publishing, and CI runs the same tiers — catching a failure locally saves a build and a dead tag.
- The `docker` tier is new and worth running locally. `origin/main`'s workflow now has `image: needs: [test, docker-image-test]`, where `docker-image-test` builds `Dockerfile.eve-sandbox` fresh and asserts a real `/invoke` call works. That's one more way the tag build can go red, and it needs a working local Docker daemon.

`main` is unprotected, so the direct push matches existing history.

### Step 2 — Watch the build

```bash
gh run list --workflow build.yml --limit 5 \
  --json databaseId,headBranch,event,status,conclusion
gh run watch <databaseId> --exit-status
```

One green run means all **four** images exist at `v0.3.0` from the same commit. Watch the run rather than probing GHCR — the local `gh` token has no `read:packages` scope, so `docker manifest inspect` and the packages API both fail for reasons that say nothing about the build.

If the matrix goes red: **stop, and do not move the tag.** Fix forward on `main` and cut `v0.3.1`. A moved tag means GHCR and the cluster disagree about what a version is and nothing in the lab would notice.

Expect this to take longer than past releases — the docker image test builds a container before the publish job even starts.

### Step 3 — One PR, all three pins

```bash
cd ~/GitHub/home/lab/infrastructure
git checkout main && git pull && git checkout -b eve-release-v0.3.0
for a in eve eve-tools eve-ambient; do
  sed -i '' 's/newTag: .*/newTag: v0.3.0/' \
    kubernetes/apps/$a/overlays/homelab/kustomization.yaml
done
EVE_INFRA_REPO=$PWD ~/GitHub/eve-ai/.claude/skills/release-eve/check-sync.sh v0.3.0
```

The unified branch name matters — separate `eve-v0.3.0` / `eve-tools-v0.3.0` branches is the habit that produced the ambient drift in the first place.

The check must pass on the three overlay rows before committing. The `running` rows will still show `v0.2.4` / `v0.2.0` — expected until ArgoCD syncs.

Commit with **explicit paths**, not `-a`, given the graphify-out situation:

```bash
git add kubernetes/apps/eve/overlays/homelab/kustomization.yaml \
        kubernetes/apps/eve-tools/overlays/homelab/kustomization.yaml \
        kubernetes/apps/eve-ambient/overlays/homelab/kustomization.yaml
git commit -m "eve to v0.3.0: sandboxed tools, eval harness, self-authoring"
git push -u origin eve-release-v0.3.0
gh pr create --fill-first
```

Write the PR body from `git log v0.2.4..v0.3.0 --oneline` in eve-ai — the infra diff is three identical one-line changes and says nothing about what's shipping. Call out, at minimum:

- **Alembic migrations run on this deploy.** `eve-migrate` keeps its name and contract but now shells out to `alembic upgrade head` under the same advisory lock. `0001_baseline` is idempotent (`IF NOT EXISTS` throughout) so it's a no-op against the existing database.
- **`eve-ambient` jumps v0.2.0 -> v0.3.0**, four releases of shared `src/eve` at once.
- **A fourth image (`eve-sandbox`) now exists but is not deployed**, and the feature is off by default.

No docs or Gatus edit is needed for this PR: a version bump adds no service and no new `*.chalifour.dev` host. That changes if you fold the `eve-sandbox` app in — then `docs/architecture.md` §4 and the Gatus config both move with it, per that repo's `CLAUDE.md`.

Merge once checks pass. **ArgoCD tracks `HEAD` of `main` with automated sync and selfHeal, so the merge is the deploy.** Nothing else to trigger.

### Step 4 — Verify the rollout

Synced is not healthy, and healthy is not "running what you shipped" — a pod that can't pull an image reports `Synced` while the old ReplicaSet keeps serving. Check the images:

```bash
kubectl -n argocd get applications.argoproj.io eve eve-tools eve-ambient
kubectl -n eve rollout status deploy/eve deploy/eve-ambient --timeout=5m
kubectl -n eve-tools rollout status deploy/eve-tools --timeout=5m
~/GitHub/eve-ai/.claude/skills/release-eve/check-sync.sh v0.3.0
```

**`check-sync.sh` passing on all eight rows is the definition of done.** That line is the report.

If ArgoCD hasn't picked up the merge within a couple of minutes:

```bash
kubectl -n argocd patch app <name> --type merge -p '{"operation":{"sync":{}}}'
```

The `argocd` CLI is installed but its session token is usually expired, so reach for `kubectl` first.

### Step 5 — Follow-up (separate PR, not this one)

1. Add the `eve-sandbox` app to infrastructure per spec §10.1 — Deployment, Service, default-deny-egress NetworkPolicy, Gatus `/healthz` check, `docs/architecture.md` §4 update.
2. Add an `EVE_SANDBOX_API_KEY` (>= 32 chars) to Vault under `credentials/eve` and wire it through the `eve` ExternalSecret, then flip `EVE_SANDBOX_ENABLED=true`.
3. Teach `check-sync.sh` about the fourth image so the invariant it enforces matches the invariant that now exists.

---

## What to watch for

- **`eve` CrashLooping right after deploy** is more often authentik than Eve. Eve refuses to start unless `EVE_AUTH_MODE=oidc` has all three `EVE_OIDC_*` values — check the `ExternalSecret` and authentik's health before assuming a bad image.
- **A failure in the `eve-migrate` initContainer is a real migration failure.** Read its logs before rolling back. This release is the one where that's a live risk: it's the first deploy on Alembic, and a partially applied schema may not be fixed by returning to the old image. ADR 0011 notes `eve_schema_version` was deliberately left in place precisely so a rollback to the previous image doesn't fail on a missing table.
- **Rollback is a forward change to the pins, never a moved tag.** Set all three overlays back to `v0.2.4` in one PR and merge. `selfHeal` reverts any `kubectl edit` on a Deployment within a minute, so live edits are diagnostics, never fixes.
- **If only one component breaks, do not bump just that one.** That's how the ambient drift got there. Cut `v0.3.1` across all three.

---

## What I need from you to start

1. **Which fix?** Confirm whether you mean PR #19 (already on `origin/main`) or your unpushed `06d4985` nostream commit — and whether to rebase and include it.
2. **Confirm `v0.3.0`** as the version.
3. **Confirm deferring `eve-sandbox`** to a follow-up PR rather than folding the new app into this release.

Once you answer those three, Step 0 onward is mechanical.
