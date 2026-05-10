#!/usr/bin/env bash
# Stop hook — detecta drift entre código y docs.
# Inyecta additionalContext si hay cambios en código sin acompañar cambios en docs/.
# No bloquea, solo recuerda.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo '')"
if [[ -z "$REPO_ROOT" ]]; then
  exit 0
fi

cd "$REPO_ROOT"

# Cambios sin commit (working tree + staged)
CHANGED=$(git status --porcelain 2>/dev/null || echo "")
if [[ -z "$CHANGED" ]]; then
  exit 0
fi

# Separar code vs docs
CODE_CHANGES=$(echo "$CHANGED" | grep -Ev '^\s*[AM\?D ]+ (docs/|DECISIONS\.md|README\.md|\.claude/)' | grep -E '\.(py|ts|js|tsx|jsx|go|rs|sql|toml|yaml|yml|json)$' || true)
DOC_CHANGES=$(echo "$CHANGED" | grep -E '^\s*[AM\?D ]+ (docs/|DECISIONS\.md|README\.md)' || true)

# Si hay cambios de código pero ningún cambio en docs, recordar
if [[ -n "$CODE_CHANGES" && -z "$DOC_CHANGES" ]]; then
  CODE_COUNT=$(echo "$CODE_CHANGES" | wc -l | tr -d ' ')
  cat <<EOF
{
  "decision": "approve",
  "hookSpecificOutput": {
    "hookEventName": "Stop",
    "additionalContext": "📝 doc-keeper drift check: ${CODE_COUNT} archivo(s) de código modificado(s) sin cambios en docs/. Considera ejecutar /sync-docs antes de cerrar la sesión para mantener la documentación alineada con git."
  }
}
EOF
  exit 0
fi

exit 0
