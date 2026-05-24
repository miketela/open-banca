#!/usr/bin/env bash
# scripts/bootstrap_env.sh — crea .env desde .env.example y rellena secretos locales (HU02).
#
# Uso (desde la raíz del repo):
#   ./scripts/bootstrap_env.sh
#
# - Copia .env.example → .env solo si .env no existe (nunca sobrescribe .env existente).
# - Genera valores aleatorios (Python secrets) para vars aún en placeholder:
#     OPEN_BANCA_MASTER_PASSPHRASE, WEBHOOK_HMAC_SECRET, API_KEY, TEMPORAL_DB_PASSWORD
# - Recuerda configurar ANTHROPIC_API_KEY manualmente con una key real.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

EXAMPLE="${REPO_ROOT}/.env.example"
TARGET="${REPO_ROOT}/.env"

if [[ ! -f "${EXAMPLE}" ]]; then
    echo "error: missing ${EXAMPLE}" >&2
    exit 1
fi

if [[ ! -f "${TARGET}" ]]; then
    cp "${EXAMPLE}" "${TARGET}"
    echo "created ${TARGET} from .env.example"
else
    echo "${TARGET} already exists — not overwriting (copy step skipped)"
fi

# Rellena solo placeholders para no pisar secretos ya configurados por el operador.
REPO_ROOT_FOR_PY="${REPO_ROOT}" uv run python - <<'PY'
from __future__ import annotations

import os
import secrets
from pathlib import Path

root = Path(os.environ["REPO_ROOT_FOR_PY"])
path = root / ".env"
text = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)


def _strip_val(raw: str) -> str:
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        s = s[1:-1]
    return s


def needs_placeholder(value: str) -> bool:
    """Only replace .env.example template literals, not operator-edited values."""
    v = _strip_val(value)
    if not v:
        return True
    # Exact templates shipped in .env.example (avoid regenerating partial edits).
    example_templates = {
        "CHANGE_ME_use_python_secrets_token_urlsafe_32",
        "CHANGE_ME_use_python_secrets_token_hex_32",
        "CHANGE_ME_use_python_secrets_token_urlsafe_24",
    }
    return v in example_templates


def gen_for_key(key: str) -> str:
    if key == "OPEN_BANCA_MASTER_PASSPHRASE":
        return secrets.token_urlsafe(32)
    if key == "WEBHOOK_HMAC_SECRET":
        return secrets.token_hex(32)
    if key == "API_KEY":
        return secrets.token_urlsafe(32)
    if key == "TEMPORAL_DB_PASSWORD":
        return secrets.token_urlsafe(24)
    raise KeyError(key)


SECRET_ENV_KEYS = {
    "OPEN_BANCA_MASTER_PASSPHRASE",
    "WEBHOOK_HMAC_SECRET",
    "API_KEY",
    "TEMPORAL_DB_PASSWORD",
}

out: list[str] = []
for line in text:
    stripped = line.lstrip()
    if stripped.startswith("#") or "=" not in line:
        out.append(line)
        continue
    key, _, rest = line.partition("=")
    k = key.strip()
    if k not in SECRET_ENV_KEYS:
        out.append(line)
        continue
    val_part = rest.rstrip("\r\n")
    trailing = rest[len(val_part) :]
    if not needs_placeholder(val_part):
        out.append(line)
        continue
    new_val = gen_for_key(k)
    out.append(f"{k}={new_val}{trailing}")

path.write_text("".join(out), encoding="utf-8")
PY

echo ""
echo "========================================================================"
echo "Siguiente paso obligatorio: edita .env y asigna ANTHROPIC_API_KEY con tu key real"
echo "(formato sk-ant-...). Sin clave válida el worker y el mapper no podrán llamar a Anthropic."
echo "Opcional: DEEPSEEK_API_KEY, URL de webhook de prueba, overrides OPEN_BANCA_*_MODEL, etc."
echo "========================================================================"
