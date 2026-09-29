#!/usr/bin/env bash
# Build tempo-server at exactly the pinned tempo commit into .tools/bin/ (gitignored).
#
# The runner prefers tempo.serve from the pinned Python package; this binary is the fallback
# (and what TEMPO_SERVER_BIN usually points at). Same SHA as pyproject's `tempo` extra, so the
# server that plays the matches is the engine that verifies them.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
sha="$(grep -oE 'tempoarena/tempo@[0-9a-f]{40}' "$here/pyproject.toml" | head -1 | cut -d@ -f2)"
[[ -n "$sha" ]] || { echo "no tempo pin found in pyproject.toml"; exit 1; }
cargo install --quiet --locked --git https://github.com/tempoarena/tempo --rev "$sha" \
  tempo-server --root "$here/.tools"
echo "$sha" > "$here/.tools/tempo-server.sha"
echo "built $here/.tools/bin/tempo-server at tempo $sha"
