#!/usr/bin/env bash
# scripts/validate_hu03_smoke.sh — HU03 smoke E2E curl cookbook (NO ejecuta scrape live)
#
# Imprime comandos curl documentados para el flujo scrape → OTP → result → incremental.
# Requiere stack HU02 levantado y credenciales en vault.
#
# Uso:
#   export API_KEY='...'                    # mismo valor que .env API_KEY
#   export CREDENTIAL_REF='banco_general:...'  # ref del vault (CLI o POST /credentials)
#   export WEBHOOK_URL='https://webhook.site/...'  # opcional
#   bash scripts/validate_hu03_smoke.sh           # solo imprime comandos
#   bash scripts/validate_hu03_smoke.sh --run     # ejecuta hasta POST /scrape (sin OTP live)
#
# NO ejecuta OTP ni scrape contra banco real salvo que el operador confirme manualmente.

set -euo pipefail

API_PORT="${API_PORT:-8080}"
API_BASE="${API_BASE:-http://localhost:${API_PORT}}"
RUN_MODE="${1:-}"

if [[ -z "${API_KEY:-}" ]]; then
  echo "ERROR: export API_KEY antes de continuar (valor de .env API_KEY)" >&2
  exit 1
fi

AUTH_HEADER="Authorization: Bearer ${API_KEY}"
IDEM_KEY="hu03-smoke-$(date +%Y%m%d-%H%M%S)"
CREDENTIAL_REF="${CREDENTIAL_REF:-banco_general:CHANGE_ME}"
WEBHOOK_URL="${WEBHOOK_URL:-}"

cat <<EOF
# =============================================================================
# HU03 — Smoke E2E Banco General (curl cookbook)
# API: ${API_BASE}
# Auth: Bearer \$API_KEY | Idempotency-Key en POST /scrape
# Spec: specs/dev-specs/HU03-smoke-e2e-banco-general.md
# Evidencia: docs/06-banks/banco-general-e2e-evidence.md
# =============================================================================

# 0) Prerrequisitos
curl -fsS ${API_BASE}/healthz
curl -fsS ${API_BASE}/readyz
docker compose ps

# 1) Registrar credenciales (alternativa CLI)
# docker compose exec temporal-worker open-banca register-credentials --bank banco_general
# O POST /credentials (si expuesto):
# curl -X POST ${API_BASE}/credentials \\
#   -H "${AUTH_HEADER}" \\
#   -H "Content-Type: application/json" \\
#   -d '{"bank_id":"banco_general","label":"smoke","username":"...","password":"..."}'

# 2) POST /scrape — full_historical (primera corrida)
export JOB_ID=\$(curl -sS -X POST ${API_BASE}/scrape \\
  -H "${AUTH_HEADER}" \\
  -H "Content-Type: application/json" \\
  -H "Idempotency-Key: ${IDEM_KEY}" \\
  -d '{
    "bank_id": "banco_general",
    "credentials": "${CREDENTIAL_REF}",
    "mode": "full",
    "webhook_url": "${WEBHOOK_URL:-https://webhook.site/CHANGE_ME}"
  }' | jq -r .job_id)
echo "Job ID: \$JOB_ID"

# 3) Poll status
watch -n 5 "curl -s ${API_BASE}/jobs/\$JOB_ID -H '${AUTH_HEADER}' | jq .status"

# 4) OTP — confirmar push en device físico, luego:
curl -X POST ${API_BASE}/jobs/\$JOB_ID/otp-confirmed \\
  -H "${AUTH_HEADER}"

# Alternativa human-input (prompt_user steps):
# curl -X POST ${API_BASE}/jobs/\$JOB_ID/human-input \\
#   -H "${AUTH_HEADER}" \\
#   -H "Content-Type: application/json" \\
#   -d '{"step_id":"otp","answer":"confirmed"}'

# 5) Esperar webhook job.completed (≤5 min post-OTP). Verificar HMAC:
# echo -n "<payload>" | openssl dgst -sha256 -hmac "\$WEBHOOK_HMAC_SECRET"

# 6) GET result
curl -s ${API_BASE}/jobs/\$JOB_ID/result \\
  -H "${AUTH_HEADER}" | jq .

# 7) GET cursor (post-completed)
curl -s ${API_BASE}/jobs/\$JOB_ID/cursor \\
  -H "${AUTH_HEADER}" | jq .

# 8) Segunda corrida incremental
curl -sS -X POST ${API_BASE}/scrape \\
  -H "${AUTH_HEADER}" \\
  -H "Content-Type: application/json" \\
  -H "Idempotency-Key: hu03-incremental-\$(date +%s)" \\
  -d '{
    "bank_id": "banco_general",
    "credentials": "${CREDENTIAL_REF}",
    "mode": "incremental"
  }' | jq .

# 9) Canary post-corrida
# uv run pytest -m canary -q
# docker compose logs --no-color | grep -iE "(password|cedula)" && echo "LEAK!" || echo "OK"

EOF

if [[ "${RUN_MODE}" == "--run" ]]; then
  echo ""
  echo "# --- Ejecutando prerrequisitos + POST /scrape (sin OTP) ---"
  curl -fsS "${API_BASE}/healthz" && echo " healthz OK"
  curl -fsS "${API_BASE}/readyz" && echo " readyz OK" || echo " readyz not ready (continuando)"
  if [[ "${CREDENTIAL_REF}" == *"CHANGE_ME"* ]]; then
    echo "ERROR: configure CREDENTIAL_REF real antes de --run" >&2
    exit 1
  fi
  RESP=$(curl -sS -X POST "${API_BASE}/scrape" \
    -H "${AUTH_HEADER}" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: ${IDEM_KEY}" \
    -d "{\"bank_id\":\"banco_general\",\"credentials\":\"${CREDENTIAL_REF}\",\"mode\":\"full\",\"webhook_url\":\"${WEBHOOK_URL}\"}")
  echo "${RESP}" | jq .
  echo "Para continuar con OTP live, use los comandos impresos arriba."
fi
