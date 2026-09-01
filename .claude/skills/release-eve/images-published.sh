#!/usr/bin/env bash
# Assert every image in the build matrix exists in GHCR at a given tag.
#
#   images-published.sh v0.2.5
#
# This is the release gate between tagging and touching the infra repo, and it
# asks the registry directly rather than trusting a green CI run: a workflow
# can go green on a rerun, a matrix leg can be skipped, and a partially
# published release is exactly the state that produces drift downstream.
#
# GHCR hands out anonymous pull tokens for public packages, so no auth is
# needed -- and notably `gh api .../packages` does NOT work here (the local
# token has no read:packages scope), which is misleading enough to be worth
# naming. A package that has never been published at all (eve-sandbox, until
# its first tagged build) returns DENIED rather than an empty tag list.
set -uo pipefail

EVE_REPO="${EVE_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && git rev-parse --show-toplevel)}"
OWNER="${EVE_GHCR_OWNER:-noahchalifour}"
WANT="${1:?usage: images-published.sh <tag>, e.g. v0.2.5}"

fail=0
for img in $(awk '/^ *- image: /{print $3}' "$EVE_REPO/.github/workflows/build.yml"); do
  tok=$(curl -sf "https://ghcr.io/token?scope=repository:$OWNER/$img:pull&service=ghcr.io" \
        | python3 -c 'import sys,json; print(json.load(sys.stdin).get("token",""))' 2>/dev/null)
  tags=$(curl -sf -H "Authorization: Bearer $tok" \
         "https://ghcr.io/v2/$OWNER/$img/tags/list" \
         | python3 -c 'import sys,json; print("\n".join(json.load(sys.stdin).get("tags") or []))' 2>/dev/null)
  if printf '%s\n' "$tags" | grep -qx "$WANT"; then
    printf '%-14s %s ok\n' "$img" "$WANT"
  elif [[ -z $tags ]]; then
    printf '%-14s MISSING (package not published at all)\n' "$img"; fail=1
  else
    printf '%-14s MISSING %s (has: %s)\n' "$img" "$WANT" \
      "$(printf '%s\n' "$tags" | sort | tail -3 | paste -sd, -)"; fail=1
  fi
done
[[ $fail -ne 0 ]] && { echo; echo "NOT all images published at $WANT"; exit 1; }
echo; echo "all images published at $WANT"
