#!/usr/bin/env bash
# Report every place an Eve version is pinned, and fail if they disagree.
#
# A release is one commit becoming N images that land on the cluster together.
# The version then hides in a pin per image per layer -- pyproject, the git
# tag, a kustomize overlay each, a running workload each -- and nothing but
# this check couples them. That is how eve-ambient sat on v0.2.0 for four
# releases while eve and eve-tools moved to v0.2.4.
#
# The image list is read from .github/workflows/build.yml's publish matrix,
# never hardcoded: that matrix is what actually gets built, so an image added
# there (eve-sandbox was, in Phase 5c) shows up here on its own instead of
# being silently skipped by a check written when there were three.
#
# Usage:
#   check-sync.sh              # all pins must agree with each other
#   check-sync.sh v0.2.5       # all pins must equal v0.2.5
#
# Env: EVE_REPO, EVE_INFRA_REPO override the repo locations.
#      EVE_SKIP_LIVE=1 drops the cluster rows. Use it between editing the
#      overlays and ArgoCD syncing, where the pods are *correctly* still on
#      the old tag -- otherwise that window reports a red that means nothing,
#      and a check you learn to ignore is worse than no check.
set -uo pipefail

EVE_REPO="${EVE_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && git rev-parse --show-toplevel)}"
INFRA_REPO="${EVE_INFRA_REPO:-$HOME/GitHub/home/lab/infrastructure}"
WANT="${1:-}"

fail=0
declare -a rows notes

record() {  # record <label> <value> [advisory]
  rows+=("$1|$2")
  if [[ -z "${3:-}" && "$2" != "-" && -n "$WANT" && "$2" != "$WANT" ]]; then fail=1; fi
  return 0
}

# The image that ships the app is ghcr.io/.../eve-ai but the k8s app, its
# namespace and its Deployment are all called `eve`. Every other image's name
# is used verbatim. Keep this the only place that asymmetry is encoded.
app_of() { [[ $1 == eve-ai ]] && echo eve || echo "$1"; }

images=$(awk '/^ *- image: /{print $3}' "$EVE_REPO/.github/workflows/build.yml")
[[ -z $images ]] && { echo "cannot read the build matrix in .github/workflows/build.yml" >&2; exit 2; }

# --- source of truth: the eve-ai repo -------------------------------------
py=$(grep -m1 '^version = ' "$EVE_REPO/pyproject.toml" | cut -d'"' -f2)
record "pyproject.toml" "v${py:-?}"
# uv.lock carries the project's own version too. It is checked here because
# CI's `uv sync --frozen` fails on a stale lock *before* the image job runs --
# so forgetting `uv lock` costs a published tag with no images behind it.
lk=$(awk '/^name = "eve"$/{getline; print; exit}' "$EVE_REPO/uv.lock" | cut -d'"' -f2)
record "uv.lock" "v${lk:-?}"
record "latest git tag" "$(git -C "$EVE_REPO" tag --sort=-v:refname | head -1)"

# --- what GitOps will deploy: the infra overlays ---------------------------
for img in $images; do
  app=$(app_of "$img")
  f="$INFRA_REPO/kubernetes/apps/$app/overlays/homelab/kustomization.yaml"
  if [[ -f $f ]]; then
    record "overlay $app" "$(awk '/newTag:/ {print $2; exit}' "$f")"
  else
    # Built but not deployed anywhere. Not a sync failure -- an image can
    # legitimately ship before its app exists -- but it must be visible,
    # because "nobody noticed it" is the whole failure mode here.
    record "overlay $app" "(no infra app)" advisory
    notes+=("$img is built by CI but has no kubernetes/apps/$app overlay -- it deploys nowhere")
  fi
done

# --- what is actually running ---------------------------------------------
# Advisory: a laptop off the LAN has no cluster, and that is not a release
# failure. Absent rows print as "-" and never set the exit code.
if [[ -z "${EVE_SKIP_LIVE:-}" ]]; then
  live=$(kubectl get deploy -A \
    -o custom-columns='NS:.metadata.namespace,N:.metadata.name,I:.spec.template.spec.containers[*].image' \
    --no-headers 2>/dev/null)
  for img in $images; do
    app=$(app_of "$img")
    tag=$(printf '%s\n' "$live" | awk -v a="$app" '$2==a {n=split($3,p,":"); print p[n]}')
    record "running $app" "${tag:--}"
  done
fi

printf '%-20s %s\n' "WHERE" "VERSION"
for r in "${rows[@]}"; do printf '%-20s %s\n' "${r%%|*}" "${r##*|}"; done
for n in "${notes[@]:-}"; do [[ -n $n ]] && echo "note: $n"; done

# Without an expected version, agreement among the real pins is the test.
if [[ -z "$WANT" ]]; then
  seen=$(for r in "${rows[@]}"; do v="${r##*|}"
    [[ $v != "-" && $v != "(no infra app)" ]] && echo "$v"; done | sort -u | wc -l)
  [[ $seen -gt 1 ]] && fail=1
fi

if [[ $fail -ne 0 ]]; then
  echo; echo "OUT OF SYNC${WANT:+ (expected $WANT)}"; exit 1
fi
echo; echo "in sync${WANT:+ at $WANT}"
