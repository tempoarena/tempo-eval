#!/usr/bin/env bash
# Pin tempo-eval to a tempo commit: scripts/bump-tempo.sh [<sha>]   (default: tempo origin/main)
#
# The pin lives in exactly one place, the `tempo` extra in pyproject.toml; every result also
# records the git SHA the server reports, so a leaderboard names the engine that produced it.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
sha="${1:-$(git -C "$here/../tempo" fetch -q origin && git -C "$here/../tempo" rev-parse origin/main)}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "need a full 40-char sha, got '$sha'"; exit 1; }
sed -i -E "s#(tempoarena/tempo(\.git)?@)[0-9a-f]{7,40}#\1$sha#g" "$here/pyproject.toml"
grep -n "tempoarena/tempo" "$here/pyproject.toml"
cd "$here" && uv lock -q && echo "pinned tempo $sha (commit pyproject.toml + uv.lock)"
