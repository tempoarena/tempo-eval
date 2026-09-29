#!/usr/bin/env bash
# Use a local tempo checkout (default ../tempo) instead of the pinned git revision.
#
#   scripts/dev-link.sh            # editable installs of ../tempo and ../tempo/baselines
#   scripts/dev-link.sh ~/src/tempo
#
# Builds tempo's native module with maturin (needs Rust). Afterwards run commands with
# `uv run --no-sync ...` (or `uv sync --inexact`): a plain `uv sync` puts the pinned
# revision back, which is how you undo this.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
tempo="$(cd "${1:-$here/../tempo}" && pwd)"
cd "$here"
uv sync --inexact -q
pkgs=(-e "$tempo")
[[ -f "$tempo/baselines/pyproject.toml" ]] && pkgs+=(-e "$tempo/baselines")
uv pip install "${pkgs[@]}"
uv run --no-sync python -c "import tempo; print('tempo', getattr(tempo, '__version__', '?'), 'from', tempo.__file__)"
