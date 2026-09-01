Here's the full plan. Nothing below has been executed — read it first, especially **Step 0**, because the starting state isn't quite what you described.

---

## Before anything: the fix is not on GitHub main yet

I checked `noahchalifour/eve-ai` and the local clone. What's actually true right now:

| Thing | State |
|---|---|
| Your fix `06d4985` — *"fix(memory): tag background REFLEX calls nostream so they don't leak into the client stream"* | **Local only.** Not on `origin/main`, no PR, no CI run. |
| Local `main` | 1 ahead (`06d4985`), **24 behind** `origin/main` |
| `origin/main` | `77dea8c` = merge of PR #19 (Phase 5c). CI green. |
| Latest tag | `v0.2.4` → commit `9ab31c4` (PR #15, the Monarch budgets fix, **2026-08-27**) |
| Cluster right now | `eve` = `ghcr.io/noahchalifour/eve-ai:v0.2.4`, `eve-ambient` = `v0.2.0`, all ArgoCD apps `Synced`/`Healthy` at infra rev `dec36f0` |

So your local `main` branched *before* PR #18 (claude-sonnet-5 fallback) and PR #19 (Phase 5c) landed. Good news: I dry-ran the merge with `git merge-tree` and **the fix applies onto `origin/main` cleanly — no conflicts**, and nothing on `origin/main` has touched `src/eve/memory/extract.py` since your base.

### The bigger thing to decide

The cluster is on `v0.2.4`. `origin/main` today contains **Phase 5a (self-improvement), 5b (eval harness), and 5c (gated tool code)** on top of that. Cutting a tag from `main` deploys all of it, not just your one-line streaming fix.

I dug into whether that's safe, and it looks fine, but you should decide deliberately:

- **Every new subsystem is off by default.** `.env.example` gained 31 lines between `v0.2.4` and `main`; the switches are `EVE_SELF_AUTHORING_ENABLED=false`, `EVE_EVAL_HYGIENE_APPLY_ENABLED=false`, `EVE_SANDBOX_ENABLED=false`. None of them are set in `kubernetes/apps/eve/base/deployment.yaml`, so they all land at their safe defaults. The settings that *do* need values (`EVE_SANDBOX_API_KEY`, etc.) are only read when the switch is on.
- **Migrations changed shape and it's handled.** 5c moved Eve's schema from the hand-rolled `eve_schema_version` list to Alembic against a private `eve_alembic_version` table. `alembic/versions/0001_baseline.py` is entirely `IF NOT EXISTS` — a no-op against your already-migrated DB — and it deliberately leaves `eve_schema_version` in place so a rollback to `v0.2.4` still finds the table it expects. The new `Dockerfile` adds `COPY alembic.ini ./` and `COPY alembic ./alembic`, `alembic>=1.14.0` is a runtime (not dev) dependency, and the Deployment's `eve-migrate` initContainer uses the same image and tag — so it will have the files it needs.
- **A fourth image appears.** The build matrix on `main` now publishes `eve-sandbox` alongside `eve-ai`/`eve-tools`/`eve-ambient`. There is **no** `kubernetes/apps/eve-sandbox` in the infra repo and no app-of-apps entry. That's fine — with `EVE_SANDBOX_ENABLED=false` nothing calls it. Just don't be surprised by the extra GHCR package.

If you'd rather ship *only* the streaming fix, say so and I'll write up the alternative: cherry-pick `06d4985` onto `v0.2.4` and tag `v0.2.5` off that instead. It's more work and leaves main un-released, so I'd default to shipping main.

---

## How the pipeline actually works

Two repos, one handoff:

```
eve-ai (this repo)                         home-lab-infrastructure
─────────────────────                      ───────────────────────
push annotated tag v*                      bump newTag in
   ↓                                       kubernetes/apps/eve/
.github/workflows/build.yml                  overlays/homelab/
  test → docker-image-test → image            kustomization.yaml
   ↓ (tag-gated)                            ↓
ghcr.io/noahchalifour/{eve-ai,      ────►  ArgoCD (automated,
  eve-tools,eve-ambient,eve-sandbox}         prune, selfHeal)
                                             ↓
                                           eve namespace, Recreate,
                                           replicas: 1
```

Nothing else is automated. Merging to `main` does **not** publish an image (the `image` job is `if: startsWith(github.ref, 'refs/tags/v')`), and publishing an image does **not** update the cluster — the tag is pinned by hand in the infra repo.

---

## Step 0 — Get the fix onto `origin/main`

Your repo's convention is one PR per change (every commit on `main` is a merge commit, except a couple of direct pushes like `a369d33`). I'd follow it:

```bash
cd ~/GitHub/eve-ai
git fetch origin --tags

# Park the fix on its own branch before touching main
git branch fix/memory-nostream 06d4985

# Reset local main to what GitHub actually has
git checkout main
git reset --hard origin/main

# Replay the fix on top (verified conflict-free)
git checkout fix/memory-nostream
git rebase main

# Sanity check before pushing
uv run pytest -m "not integration and not live and not docker" -v

git push -u origin fix/memory-nostream
gh pr create --base main \
  --title "Tag background REFLEX calls nostream so they don't leak into the client stream" \
  --body "extract's structured-output call and the digest-refresh call both run as LangGraph nodes streamed via stream_mode=\"messages-tuple\". Without a nostream tag their raw model output (e.g. the literal {\"operations\": []} JSON) was emitted as an AIMessageChunk indistinguishable from eve's own reply."
```

Wait for the PR's `build` check to go green, then merge (squash or merge commit — both are used in this repo's history).

> **Faster alternative** if you don't want the PR ceremony: `git checkout main && git pull --rebase origin main && git push`. CI still runs on push to `main`. You lose the review record; `a369d33` was done this way.

Also note: your worktree has two untracked paths (`.claude/` and `docs/superpowers/plans/2026-08-28-background-memory-extraction.md`). Neither is in the fix commit. Decide whether the plan doc should ship with the PR — the repo does keep plan docs under `docs/superpowers/plans/`.

---

## Step 1 — Confirm `main` is green

```bash
gh run list --branch main --limit 3
gh run watch          # if one is still going
```

Three jobs must pass before a tag will publish anything: `test`, `docker-image-test` (this one actually builds `eve-sandbox` and asserts a real `/invoke` works), and then `image`. The `image` job is `needs: [test, docker-image-test]`.

---

## Step 2 — Cut and push the tag

`v0.2.4` is the latest, so this is **`v0.2.5`**. Tags in this repo are **annotated** (`git cat-file -t v0.2.4` → `tag`) with a one-line subject; there are no GitHub Releases.

```bash
cd ~/GitHub/eve-ai
git checkout main && git pull

# Confirm you're tagging the merge commit that contains the fix
git log --oneline -3

git tag -a v0.2.5 -m "Don't leak background REFLEX output into the client stream"
git push origin v0.2.5
```

Note `pyproject.toml` still says `version = "0.1.0"` and has since `v0.1.0` — it is not the source of truth for the release version and nobody bumps it. Leave it alone unless you want to start.

---

## Step 3 — Watch the image build

```bash
gh run list --limit 3
gh run watch                # ~a few minutes; four images build in parallel

# Confirm the tags landed in GHCR
gh api /users/noahchalifour/packages/container/eve-ai/versions \
  --jq '.[0].metadata.container.tags'
```

The `docker/metadata-action` default tagging turns `refs/tags/v0.2.5` into image tag `v0.2.5` (plus `0.2.5` / `0.2` semver tags). The overlay pins the `v`-prefixed form, matching what's there today.

Expect four packages: `eve-ai`, `eve-tools`, `eve-ambient`, `eve-sandbox`.

---

## Step 4 — Bump the pin in the infrastructure repo

**Your local infra clone is 1 commit behind** — `dec36f0` ("Wire anthropic/claude-sonnet-5 as litellm's tool-capable fallback (#141)") is on `origin/main` but not local. Pull first. Your worktree is also dirty with `graphify-out/` churn; don't sweep that into the release PR.

```bash
cd ~/GitHub/home/lab/infrastructure
git checkout main && git pull
git checkout -b eve-v0.2.5
```

Edit `kubernetes/apps/eve/overlays/homelab/kustomization.yaml`:

```yaml
images:
  - name: ghcr.io/noahchalifour/eve-ai
    newTag: v0.2.5      # was v0.2.4
```

**Decide about the sibling apps in the same PR:**

| App | Pinned at | Recommendation |
|---|---|---|
| `eve` | `v0.2.4` | **Bump to `v0.2.5`** — this is the one carrying your fix. |
| `eve-tools` | `v0.2.4` | Optional. 5c touched `tools_client`'s routing, but the sandbox target is a separate service and is disabled. Bumping keeps the pair in lockstep, which is the pattern the repo has followed. |
| `eve-ambient` | `v0.2.0` | Optional and the most stale. It's a separate blast radius (Home Assistant webhook, CalDAV, ntfy) — I'd do it as its own change, not bundled with a streaming fix. |

Then validate the render before you push:

```bash
kubectl kustomize kubernetes/apps/eve/overlays/homelab | grep -n 'image:'
# expect: ghcr.io/noahchalifour/eve-ai:v0.2.5   (twice — initContainer + main container)
```

Both the `eve-migrate` initContainer and the `eve` container reference the bare image name, so the kustomize `images:` transformer rewrites both. That's deliberate — the initContainer must run migrations from the *same* tag.

Docs/monitoring per the repo's `CLAUDE.md` rules:
- **Gatus:** nothing to do. `eve` is already checked at `https://eve.chalifour.dev/health` in both `kubernetes/apps/gatus/base/configmap.yaml` and `overlays/homelab/config-patch.yaml`. No new externally facing service.
- **Docs:** a pure tag bump didn't require doc edits last time — see `188d0b3` ("eve to v0.2.4: personal access tokens (#140)"), which changed exactly one line. But because this tag drags in 5a/5b/5c, I'd say so in the PR body the way `188d0b3` did, and consider a line in `kubernetes/apps/eve/README.md` noting that 5a/5b/5c code is present but gated off.

Commit and open the PR, following the existing message shape:

```bash
git add kubernetes/apps/eve/overlays/homelab/kustomization.yaml
git commit   # title: "eve to v0.2.5: stop background memory extraction leaking into the stream"
git push -u origin eve-v0.2.5
gh pr create --base main
```

The PR body should note that `v0.2.5` also carries Phases 5a/5b/5c, that every new subsystem is off by default, and that migration is handled by the idempotent Alembic baseline in the `eve-migrate` initContainer — no manual schema step, same as `188d0b3`.

---

## Step 5 — Let ArgoCD reconcile

`kubernetes/app-of-apps/eve.yaml` has `syncPolicy.automated` with `prune: true` and `selfHeal: true` against `targetRevision: HEAD`, so merging the infra PR is the deploy. It'll pick it up within the default ~3 min poll.

To not wait:

```bash
argocd app sync eve
argocd app wait eve --health --timeout 300
```

Or watch it from kubectl:

```bash
kubectl get application -n argocd eve \
  -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status,REV:.status.sync.revision'
```

**Expect a brief outage.** The Deployment is `replicas: 1` with `strategy: Recreate` (pinned there because `aegra serve` migrates at startup and exactly one migrator may run). The old pod is torn down before the new one starts — Eve will be unreachable for the pull + init + startup window, probably 30–90s. Do it when nobody's mid-conversation.

---

## Step 6 — Verify

```bash
# 1. Right image
kubectl get deploy -n eve eve \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
# expect ghcr.io/noahchalifour/eve-ai:v0.2.5

# 2. Migrations succeeded — this is the step most likely to surprise you,
#    since it's the first run on Alembic
kubectl logs -n eve deploy/eve -c eve-migrate
# expect a clean 'alembic upgrade head'; the baseline is IF NOT EXISTS so it
# should be a no-op that just stamps eve_alembic_version

# 3. Pod healthy
kubectl get pods -n eve
kubectl logs -n eve deploy/eve --tail=100

# 4. Public path (Gatus checks this too)
curl -sS https://eve.chalifour.dev/health

# 5. The actual fix — send a turn that triggers memory extraction and
#    confirm no raw {"operations": ...} JSON appears in the stream
uv run python scripts/chat.py
```

Item 5 is the real acceptance test. The bug was that `extract`'s structured-output call and the digest refresh were emitted as `AIMessageChunk`s indistinguishable from Eve's reply, so anything printing every chunk printed them. Watch for stray JSON after the reply completes.

Also glance at Gatus and Langfuse (`https://langfuse.chalifour.dev`) to confirm traces are still landing — `LANGFUSE_*` config didn't change, but 5b added a *second*, separate Langfuse client (`EVE_LANGFUSE_HOST`) that isn't set in the Deployment. It's only used by `eve-eval`, which nothing in the cluster runs.

---

## If it goes wrong

Rollback is a one-line revert in the infra repo — ArgoCD does the rest:

```bash
cd ~/GitHub/home/lab/infrastructure
git revert <the eve-v0.2.5 merge commit>
git push
argocd app sync eve
```

Downgrading back to `v0.2.4` is safe on the schema side: the Alembic baseline is additive-only (`CREATE TABLE IF NOT EXISTS`) and deliberately leaves `eve_schema_version` in place *precisely* so the old image still finds it. Nothing is dropped or renamed.

Two known failure signatures worth having in front of you:
- **CrashLoop on startup** → per `kubernetes/apps/eve/README.md`, this "almost always means Authentik is not ready", not a broken image. Eve refuses to start if any `EVE_OIDC_*` is empty. Check `kubectl logs` before suspecting the tag.
- **initContainer fails** → the Alembic move is new. `kubectl logs -n eve deploy/eve -c eve-migrate` will print the full `alembic upgrade` output; `eve-migrate` raises loudly rather than continuing.

---

## Summary

1. Rebase `06d4985` onto `origin/main`, PR it, merge it. *(It's not on GitHub yet.)*
2. Confirm `build` is green on `main`.
3. `git tag -a v0.2.5 && git push origin v0.2.5`.
4. Wait for GHCR to have `eve-ai:v0.2.5`.
5. Bump `newTag` in `kubernetes/apps/eve/overlays/homelab/kustomization.yaml`, PR and merge in `home-lab-infrastructure`.
6. ArgoCD auto-syncs (or `argocd app sync eve`); ~30–90s outage from `Recreate` + `replicas: 1`.
7. Verify image, `eve-migrate` logs, `/health`, and that the JSON no longer leaks into the stream.

The one decision I'd like from you before starting: **are you happy shipping 5a + 5b + 5c along with the fix** (all gated off), or do you want a `v0.2.5` cherry-picked onto `v0.2.4` instead?
