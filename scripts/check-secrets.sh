#!/usr/bin/env bash
# Refuse to commit anything that looks like a credential. Runs as the pre-commit hook
# (`git config core.hooksPath .githooks`) and in CI over the whole tree (`--all`).
set -euo pipefail

patterns=(
  'AKIA[0-9A-Z]{16}'                                  # AWS access key id
  'ASIA[0-9A-Z]{16}'                                  # AWS temporary key id
  'aws_secret_access_key[[:space:]]*[=:]'
  'sk-[A-Za-z0-9_-]{20,}'                             # OpenAI/Anthropic-style keys
  'ts_[A-Za-z0-9]{24,}'                               # TypeSafe-style keys
  'apikey_[A-Za-z0-9_]{32,}'                          # TypeSafe API keys
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  '(api[_-]?key|secret|token|password)["'"'"']?[[:space:]]*[=:][[:space:]]*["'"'"'][A-Za-z0-9_\-\.]{16,}["'"'"']'
)
regex=$(IFS='|'; echo "${patterns[*]}")

if [[ "${1:-}" == "--all" ]]; then
  files=$(git ls-files)
else
  files=$(git diff --cached --name-only --diff-filter=ACM)
fi

status=0
for f in $files; do
  case "$f" in
    .env|.env.*) [[ "$f" == ".env.example" ]] || { echo "refusing to commit $f"; status=1; continue; } ;;
    scripts/check-secrets.sh|*.lock|*/uv.lock|Cargo.lock|package-lock.json|pnpm-lock.yaml) continue ;;
  esac
  [[ -f "$f" ]] || continue
  if grep -EnI "$regex" "$f" >/dev/null 2>&1; then
    echo "possible secret in $f:"; grep -EnI "$regex" "$f" | cut -c1-120 | sed 's/^/  /'
    status=1
  fi
done
if [[ $status -ne 0 ]]; then
  echo "commit blocked by scripts/check-secrets.sh. Move the value to .env (gitignored)."
fi
exit $status
