# eve-ambient is pinned to v0.2.0 — nothing failed, the deploy bump was just never made

## Short answer

Your CI is fine. It built and pushed `eve-ambient:v0.2.1` through `v0.2.4` exactly
like it did for `eve-ai` and `eve-tools` — all four tags are sitting in GHCR right
now, unused. What never happened is the *other half* of a release: the image-tag
bump in `home-lab-infrastructure`.

The three apps are three separate Argo CD Applications with three separate
kustomize overlays, each pinning its own tag. Every release since v0.2.0 bumped
only the overlay for the app whose bug motivated the release. `eve-ambient`'s
overlay hasn't been touched since it was first created.

## The evidence

Running in the cluster:

| Workload | Namespace | Image |
|---|---|---|
| `eve` | `eve` | `ghcr.io/noahchalifour/eve-ai:v0.2.4` |
| `eve-tools` | `eve-tools` | `ghcr.io/noahchalifour/eve-tools:v0.2.4` |
| `eve-ambient` | `eve` | `ghcr.io/noahchalifour/eve-ambient:v0.2.0` |

All three Argo CD apps report **Synced / Healthy** against the same repo revision
(`dec36f0`), which is the tell: the cluster matches git exactly. Git is what's
stale, not the cluster. Argo has `automated: {prune: true, selfHeal: true}` on all
three, so there's no drift and nothing to "re-sync" — it's already sync'd to the
wrong number.

The pin lives here:

`home-lab-infrastructure` → `kubernetes/apps/eve-ambient/overlays/homelab/kustomization.yaml`

```yaml
images:
  - name: ghcr.io/noahchalifour/eve-ambient
    newTag: v0.2.0        # eve and eve-tools both say v0.2.4
```

Commit history of the three overlay files tells the whole story:

```
eve/overlays/homelab/kustomization.yaml
  188d0b30  2026-08-27  eve to v0.2.4: personal access tokens (#140)
  5cfbd111  2026-08-24  Deploy Eve's Phase 4 (Ambient) subsystem (#126)

eve-tools/overlays/homelab/kustomization.yaml
  7db84ec4  2026-08-27  eve-tools to v0.2.4: narrow the Monarch budgets query (#139)
  0f34809b  2026-08-27  eve-tools to v0.2.3: pin gql below 4 (#138)
  dd0c5f19  2026-08-25  eve-tools to v0.2.2 (#134)
  a6da67ac  2026-08-25  eve-tools to v0.2.1: Monarch's new domain and MFA (#133)
  5cfbd111  2026-08-24  Deploy Eve's Phase 4 (Ambient) subsystem (#126)

eve-ambient/overlays/homelab/kustomization.yaml
  5cfbd111  2026-08-24  Deploy Eve's Phase 4 (Ambient) subsystem (#126)     <-- and that's it
```

Four release PRs, none of them touched ambient. And the build side was never the
problem — every tag run succeeded on all three matrix legs:

```
run 33092186941 (v0.2.4):  test success
                           image (eve-ai, Dockerfile)                      success
                           image (eve-tools, Dockerfile.eve-tools)         success
                           image (eve-ambient, Dockerfile.eve-ambient)     success
```

and GHCR confirms it:

```
eve-ambient tags: v0.2.0, v0.2.1, v0.2.2, v0.2.3, v0.2.4, latest
```

So `v0.2.4` has been ready to deploy since 2026-08-27. It just was never asked for.

## The good news: you haven't actually been running broken code

I diffed `v0.2.0..v0.2.4` against what `Dockerfile.eve-ambient` actually copies
into the image (`src/eve`, `src/eve_ambient`, `prompts`, `family.yaml`, plus the
shared `uv.lock` dependency tree).

- `src/eve_ambient/` — **zero changes** across all four releases.
- `src/eve/` — three files changed: `auth.py`, `pat.py` (new), `memory/db.py`.
  - `eve_ambient` imports `eve.family`, `eve.settings`, `eve.models`, `eve.state`,
    `eve.tools_client`, `eve.memory.store`, `eve.memory.db.get_pool`, and
    `eve.specialists.permissions`. It imports **neither `eve.auth` nor `eve.pat`** —
    the PAT work is entirely on the Aegra request path, which ambient never touches.
  - `memory/db.py`'s only functional change is a new `0004_pat` entry in the
    `MIGRATIONS` list. Ambient never runs migrations — those run in `eve`'s
    `eve-migrate` initContainer, which is already on v0.2.4. So the `eve_pat` table
    exists, and ambient's stale copy of the list is inert.
- `uv.lock` changed only to pin `gql < 4`, which matters to `eve_tools/monarch.py`
  and nothing ambient imports.

**Net: `eve-ambient:v0.2.0` and `v0.2.4` are behaviourally identical.** You got
lucky — every release in that window was Monarch/eve-tools or Aegra-auth work.
This is a latent process bug that hasn't bitten yet, not a live outage. Worth
fixing precisely *because* the next release might not be so tidy.

## The right way to fix it

### 1. The immediate fix — one line, one PR

In `home-lab-infrastructure`, `kubernetes/apps/eve-ambient/overlays/homelab/kustomization.yaml`:

```yaml
images:
  - name: ghcr.io/noahchalifour/eve-ambient
    newTag: v0.2.4      # was v0.2.0
```

Open it as a PR titled in the same house style as the others
(`eve-ambient to v0.2.4: catch up to the repo-wide tag`), merge, and Argo's
automated sync picks it up on its own. Nothing else needs doing — no `kubectl`,
no manual sync, no image pull secret changes.

Two things to *not* do while you're in there:

- Don't add `CreateNamespace=true` to the eve-ambient Application. It deliberately
  doesn't own the `eve` namespace (it lives there for the `eve-db-app`
  `secretKeyRef`, which can't cross namespaces). That's documented in the app's
  README and its base kustomization.
- Don't touch `EVE_AMBIENT_ENABLED`. It's currently `"true"` in the live
  deployment and the poll loop is running.

**Rollout shape:** one replica, `strategy: Recreate`, so there's a genuine gap of a
few seconds with no pod. During that gap Home Assistant webhook POSTs to
`172.24.3.152:8091` get connection-refused and are lost (HA won't retry). The poll
loop is safe across restarts — seen/primed state lives in the `eve_ambient_seen`
table in Postgres, not in process memory, so you won't get a re-notify storm on
startup. Do it at a quiet hour if you care about the webhook gap at all.

### 2. The durable fix — stop letting the three drift

The root cause is structural: **you cut one repo-wide git tag, CI builds three
images from it, but the deploy is three independent hand-written bumps.** The
version number is inherently shared; the bump procedure isn't. That asymmetry will
keep producing this.

Options, cheapest first:

- **A release checklist that bumps all three overlays in a single PR.** Since one
  tag always produces all three images, treat "release v0.2.5" as one atomic infra
  PR touching three files, not three PRs touching one each. This is the smallest
  change and it kills the failure mode outright. Given the `release-eve` skill
  directory already in `eve-ai/.claude/skills/`, this is probably the thing to
  encode there.
- **Collapse the tag to one place.** Put `newTag` in a shared kustomize component
  that all three overlays reference, so there's exactly one line to edit per
  release. More refactoring, but it makes the drift structurally impossible rather
  than merely procedurally discouraged. The tradeoff is you lose the ability to
  hold one component back deliberately — which, given they're built from one
  lockfile and share `src/eve`, you arguably shouldn't want anyway.
- **Automate it.** Either an `eve-ai` CI job that opens the infra bump PR on tag
  push, or Argo CD Image Updater watching GHCR. More moving parts and more ways to
  surprise yourself at 2am; I'd only reach for this if releases get frequent.

My recommendation: do (1) now to unblock, then (A) — encode "bump all three in one
PR" into the release runbook. Consider (B) if you find yourself doing this monthly.

### 3. A cheap safety net

A one-liner worth having in the runbook, or as a Gatus/cron check:

```bash
kubectl get deploy -A -o json \
  | jq -r '.items[].spec.template.spec.containers[].image' \
  | grep noahchalifour/eve
```

If those three tags aren't identical, a release is half-landed.

## Separately — something actually broken right now

While reading `eve-ambient`'s logs I found live failures that are **not** caused by
the version drift, and won't be fixed by bumping it:

```
2026-08-28T22:50:48Z  eve-tools reported: error: 401, Unauthorized, url='https://api.monarch.com/graphql'
2026-08-28T22:50:48Z  source finances poll failed for member ''
                      SourceUnavailable: finances.list_transactions did not return usable JSON

2026-08-28T22:50:48Z  eve-tools reported: error: 'b96297cfe2cd...'
2026-08-28T22:50:48Z  source mail poll failed for member 'b96297cfe2cd...'
                      SourceUnavailable: mail.list_messages did not return usable JSON
```

Both the finances and mail sources have been failing every poll tick. These come
back from **eve-tools, which is already on v0.2.4** — it has all the Monarch domain,
MFA and `gql` fixes. A 401 from `api.monarch.com` on current code points at an
expired or invalid Monarch session token in Vault, not at stale code. The mail
error is a bare key that looks like a member `sub`, which smells like a missing
Gmail credential for that member rather than a request failure.

So the ambient pipeline is up, healthy, and quietly getting nothing from two of its
four sources. That's probably the more urgent thing on this list — the version pin
is cosmetic today, this isn't. Happy to dig into either.
