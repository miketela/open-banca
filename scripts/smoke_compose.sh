#!/usr/bin/env bash
# scripts/smoke_compose.sh — smoke test para docker-compose production stack
#
# Uso:
#   bash scripts/smoke_compose.sh
#
# El script arranca el stack en background, espera que los healthchecks pasen,
# ejecuta las verificaciones de API, y apaga el stack.
# Retorna exit code 0 si todo pasa, 1 si algo falla.
#
# Variables de entorno que pueden sobreescribirse:
#   API_PORT       Puerto de la API (default: 8080)
#   COMPOSE_FILE   Archivo compose (default: docker-compose.yml)
#   WAIT_TIMEOUT   Segundos de espera por healthchecks (default: 120)
#   KEEP_UP        Si es "1", no apagar el stack al final (útil para debug)

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_PORT="${API_PORT:-8080}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-120}"
KEEP_UP="${KEEP_UP:-0}"
API_BASE="http://localhost:${API_PORT}"

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASS=0
FAIL=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log_info()  { echo -e "${YELLOW}[INFO]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[PASS]${NC} $*"; PASS=$((PASS+1)); }
log_fail()  { echo -e "${RED}[FAIL]${NC} $*"; FAIL=$((FAIL+1)); }

cleanup() {
    if [[ "${KEEP_UP}" != "1" ]]; then
        log_info "Deteniendo stack..."
        docker compose -f "${COMPOSE_FILE}" down --remove-orphans --volumes 2>/dev/null || true
    else
        log_info "KEEP_UP=1: stack sigue corriendo en background"
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Pre-checks
# ---------------------------------------------------------------------------
log_info "Verificando pre-requisitos..."

if ! command -v docker &>/dev/null; then
    log_fail "docker no disponible — saltando smoke test"
    exit 0
fi

if ! docker info &>/dev/null; then
    log_fail "Docker daemon no disponible — saltando smoke test"
    exit 0
fi

# Verificar que existen vars mínimas o usar valores de test
if [[ -z "${OPEN_BANCA_MASTER_PASSPHRASE:-}" ]]; then
    export OPEN_BANCA_MASTER_PASSPHRASE="smoke-test-passphrase-$(date +%s)"
    log_info "OPEN_BANCA_MASTER_PASSPHRASE no definida — usando valor temporal de test"
fi
if [[ -z "${TEMPORAL_DB_PASSWORD:-}" ]]; then
    export TEMPORAL_DB_PASSWORD="smoke-test-temporal-$(date +%s)"
fi
if [[ -z "${WEBHOOK_HMAC_SECRET:-}" ]]; then
    export WEBHOOK_HMAC_SECRET="smoke-test-hmac-$(date +%s)"
fi
if [[ -z "${API_KEY:-}" ]]; then
    export API_KEY="smoke-test-api-key-$(date +%s)"
fi
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    export ANTHROPIC_API_KEY="smoke-test-anthropic-key"
fi

# ---------------------------------------------------------------------------
# Validar config sin daemon
# ---------------------------------------------------------------------------
log_info "Validando docker compose config..."
if docker compose -f "${COMPOSE_FILE}" config --quiet 2>&1; then
    log_ok "docker compose config --quiet OK"
else
    log_fail "docker compose config --quiet falló"
    exit 1
fi

# ---------------------------------------------------------------------------
# Build + arranca stack
# ---------------------------------------------------------------------------
log_info "Construyendo imagen open-banca (puede tardar en primera ejecución)..."
docker compose -f "${COMPOSE_FILE}" build api 2>&1 | tail -5

log_info "Arrancando stack en background..."
docker compose -f "${COMPOSE_FILE}" up -d 2>&1

# ---------------------------------------------------------------------------
# Esperar healthchecks
# ---------------------------------------------------------------------------
log_info "Esperando healthchecks (timeout: ${WAIT_TIMEOUT}s)..."

SERVICES=("open-banca-postgres-temporal" "open-banca-temporal" "open-banca-api")
START_TIME=$(date +%s)

for SERVICE in "${SERVICES[@]}"; do
    log_info "Esperando servicio: ${SERVICE}"
    ELAPSED=0
    while true; do
        CURRENT_TIME=$(date +%s)
        ELAPSED=$((CURRENT_TIME - START_TIME))

        if [[ "${ELAPSED}" -ge "${WAIT_TIMEOUT}" ]]; then
            log_fail "Timeout esperando ${SERVICE} (${WAIT_TIMEOUT}s)"
            docker compose -f "${COMPOSE_FILE}" logs "${SERVICE}" 2>/dev/null | tail -20
            break
        fi

        STATUS=$(docker inspect --format='{{.State.Health.Status}}' "${SERVICE}" 2>/dev/null || echo "starting")
        if [[ "${STATUS}" == "healthy" ]]; then
            log_ok "${SERVICE} healthy"
            break
        elif [[ "${STATUS}" == "unhealthy" ]]; then
            log_fail "${SERVICE} unhealthy"
            docker compose -f "${COMPOSE_FILE}" logs "${SERVICE}" 2>/dev/null | tail -20
            break
        fi

        sleep 3
    done
done

# ---------------------------------------------------------------------------
# Smoke tests de API
# ---------------------------------------------------------------------------
log_info "Esperando 5s adicionales para que la API complete startup..."
sleep 5

# Test 1: GET /healthz → 200
log_info "Test: GET /healthz"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${API_BASE}/healthz" 2>/dev/null || echo "000")
if [[ "${HTTP_CODE}" == "200" ]]; then
    log_ok "GET /healthz → 200"
else
    log_fail "GET /healthz → ${HTTP_CODE} (esperado 200)"
    docker compose -f "${COMPOSE_FILE}" logs api 2>/dev/null | tail -30
fi

# Test 2: GET /time → ISO timestamp
log_info "Test: GET /time"
RESPONSE=$(curl -s "${API_BASE}/time" 2>/dev/null || echo "")
if echo "${RESPONSE}" | grep -qE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'; then
    log_ok "GET /time → ISO timestamp: ${RESPONSE}"
else
    log_fail "GET /time → respuesta inesperada: '${RESPONSE}'"
fi

# Test 3: GET /readyz — puede fallar si Temporal worker no está listo, no es fatal
log_info "Test: GET /readyz (informativo)"
HTTP_CODE_READY=$(curl -s -o /dev/null -w "%{http_code}" "${API_BASE}/readyz" 2>/dev/null || echo "000")
if [[ "${HTTP_CODE_READY}" == "200" ]]; then
    log_ok "GET /readyz → 200 (stack completamente listo)"
else
    log_info "GET /readyz → ${HTTP_CODE_READY} (worker puede no estar conectado — normal en smoke)"
fi

# ---------------------------------------------------------------------------
# Resultado final
# ---------------------------------------------------------------------------
echo ""
echo "========================================"
echo -e "Smoke test completado: ${GREEN}${PASS} PASS${NC} / ${RED}${FAIL} FAIL${NC}"
echo "========================================"

if [[ "${FAIL}" -gt 0 ]]; then
    log_info "Logs del stack:"
    docker compose -f "${COMPOSE_FILE}" logs --no-color 2>/dev/null | tail -50
    exit 1
fi

exit 0
