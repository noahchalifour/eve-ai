# eve-ambient version drift — what happened and how to fix it

You're right, and `check-sync.sh` confirms it. Nothing changed; this is read-only.

```
WHERE                VERSION
pyproject.toml       v0.1.0
latest git tag       v0.2.4
overlay eve          v0.2.4
overlay eve-tools    v0.2.4
overlay eve-ambient  v0.2.0     <-- four releases behind
running eve          v0.2.4
running eve-tools    v0.2.4
running eve-ambient  v0.2.0
OUT OF SYNC
```

## What's going on

**eve-ambient's pin has never been touched since the day it was deployed.**

`kubernetes/apps/eve-ambient/overlays/homelab/kustomization.yaml` has exactly one
commit in its entire history — `5cfbd11`, "Deploy Eve's Phase 4 (Ambient)
subsystem (#126)", which set `newTag: v0.2.0`. Every release since then bumped
one image at a time, in its own PR, and none of them included ambient:

| PR | What moved | eve-ambient |
|---|---|---|
| #126 | all three → v0.2.0 | v0.2.0 |
| #133 | eve-tools → v0.2.1 | untouched |
| #134 | eve-tools → v0.2.2 | untouched |
| #138 | eve-tools → v0.2.3 | untouched |
| #139 | eve-tools → v0.2.4 | untouched |
| #140 | eve → v0.2.4 | untouched |

So this isn't a failed rollout or a stuck sync — ArgoCD reports all three apps
`Synced / Healthy` and it's telling the truth. It is faithfully deploying
v0.2.0, because that is what git says to deploy. The drift is in the manifest,
not in the cluster, which is exactly why nothing alarmed on it. There is no
check in either repo that couples the three pins except `check-sync.sh`.

Worth noting `eve` itself skipped v0.2.1–v0.2.3 too; it just caught up in #140.
Ambient never got its catch-up PR.

## Is it actually hurting anything right now?

Mostly no — you got lucky on the content of those four releases.

eve-ambient imports `eve.family`, `eve.settings`, `eve.models`,
`eve.memory.store`, `eve.memory.db`, `eve.state`, `eve.specialists.permissions`
and `eve.tools_client` out of the shared `src/eve`. The only shared files that
changed between v0.2.0 and v0.2.4 are `auth.py`, `pat.py` and `memory/db.py` —
and the `db.py` change is purely the additive `0004_pat` migration. Nothing
ambient reads changed behaviour.

On the HTTP side, eve-tools moved a lot (Monarch token auth, new domain + TOTP,
`gql<4` pin, narrowed budgets query) but the tool contract ambient consumes
didn't: `finances.list_transactions` and `finances.get_budgets` still return the
same `{id, category, period, spent, limit}` shape — v0.2.4's `get_budgets`
docstring explicitly says it normalizes "to exactly what
`eve_ambient.sources.finances` expects."

**The structural hazard is real though, and the next release is where it bites.**
Ambient does not run migrations — `migrate()` is only called by the `eve-migrate`
initContainer on `deploy/eve`. So the ambient *tables* always advance with
**eve's** version while the ambient *code that reads them* sits at its own. Today
the schema is at `0004_pat`, which ambient doesn't touch. But `main` already
carries `0005_eval`, which does `ALTER TABLE eve_ambient_notice ADD COLUMN
replied_at` and creates `eve_ambient_decision` — and `src/eve_ambient/store.py`,
`filter.py`, `pipeline.py` and `notify.py` all changed to write to them. Ship
that with ambient still at v0.2.0 and eve's initContainer creates the tables
while a v0.2.0 ambient never writes a single row into them. No crash, no error —
your entire Phase 5b eval dataset just comes back empty. That's the failure mode
this drift produces: silent, not loud.

## The right way to fix it

**A one-line PR in `home-lab-infrastructure`. No new tag, no rebuild.**

`ghcr.io/noahchalifour/eve-ambient:v0.2.4` already exists. `build.yml` publishes
all three images as a matrix on every `v*` tag, and I confirmed eve-ambient was
in that matrix at all five tags and that the v0.2.4 tag run (`33092186941`) was
green. The image has been sitting in GHCR since Aug 27 waiting for someone to
point at it.

So the heal is:

```
kubernetes/apps/eve-ambient/overlays/homelab/kustomization.yaml
  newTag: v0.2.0  ->  v0.2.4
```

one branch, one PR, merge, and ArgoCD does the rest. Since all three v0.2.4
images were built from the same commit, that restores the invariant by
definition.

Two things to do alongside it:

1. **`pyproject.toml` is still `0.1.0`** — it drifted independently while tags
   reached v0.2.4. It's one of the eight pins `check-sync.sh` reads, so even
   after the ambient PR merges the check will still fail on that row. Set it to
   `0.2.4` in a plain commit on `main` (no tag) to close it out.
2. **Verify with the script, not by eye.** After the merge:
   ```bash
   .claude/skills/release-eve/check-sync.sh v0.2.4
   ```
   All eight rows green is the definition of done. Synced ≠ healthy ≠ running
   the version you shipped.

### What not to do

- **Don't move or re-cut the v0.2.x tags.** A moved tag means GHCR and the
  cluster disagree about what a version means and nothing in the lab would
  detect it. Rollbacks and heals here are always forward changes to the pins.
- **Don't fold this into a feature release.** `main` is **43 commits** ahead of
  v0.2.4 (Phases 5a self-improvement and 5b eval harness, plus migration
  `0005_eval`). Cutting v0.3.0 right now would heal the drift, but it bundles a
  four-version catch-up with a large feature release, and if ambient then
  misbehaves you can't tell which half caused it. Heal first at v0.2.4, confirm
  ambient is happy, then release 5a/5b deliberately as its own v0.3.0 — across
  all three images.

### Stopping it recurring

The habit that caused this is visible in the branch names: `eve-v0.2.4`,
`eve-tools-v0.2.4` as separate PRs. The fix is the `release-eve` skill's shape —
one branch (`eve-release-vX.Y.Z`), one PR, all three overlays edited in the same
`for` loop, and `check-sync.sh` run before commit, after merge, and after
rollout. It's the only thing in either repo that couples the two, so it has to
be the gate rather than a habit.

Nothing has been changed. Say the word and I'll open the ambient bump PR.
