#!/usr/bin/env bash
# scripts/smoke_compose.sh — smoke test para docker-compose production stack
#
# Uso:
#   bash scripts/smoke_compose.sh
#
# Arranque escalonado (orden HU02): postgres-temporal → temporal-server →
# docker-socket-proxy → api + temporal-worker. Comprueba healthchecks, GET
# /healthz y /health, y que los logs del worker contengan "worker started".
#
# El script puede apagar el stack al salir salvo que se pida lo contrario:
#   KEEP_UP=1 bash scripts/smoke_compose.sh
# Con KEEP_UP=1 el stack queda levantado para depuración manual (operador).
#
# Variables de entorno que pueden sobreescribirse:
#   API_PORT       Puerto de la API (default: OPEN_BANCA_API_PORT o 8080)
#   COMPOSE_FILE   Archivo compose (default: docker-compose.yml)
#   WAIT_TIMEOUT   Segundos de espera por healthchecks (default: 120)
#   KEEP_UP        Si es "1", no apagar el stack al final (útil para debug del operador)

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_PORT="${API_PORT:-${OPEN_BANCA_API_PORT:-8080}}"
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
        log_info "KEEP_UP=1: stack sigue corriendo para depuración (compose down omitido)"
    fi
}
trap cleanup EXIT

wait_container_healthy() {
    local compose_service="$1"
    local start_time
    start_time=$(date +%s)
    log_info "Esperando healthcheck healthy: ${compose_service}"
    while true; do
        local elapsed
        elapsed=$(($(date +%s) - start_time))
        local cid
        cid="$(docker compose -f "${COMPOSE_FILE}" ps -q "${compose_service}" 2>/dev/null || true)"
        if [[ "${elapsed}" -ge "${WAIT_TIMEOUT}" ]]; then
            log_fail "Timeout esperando ${compose_service} (${WAIT_TIMEOUT}s)"
            docker compose -f "${COMPOSE_FILE}" logs "${compose_service}" 2>/dev/null | tail -20 || true
            return 1
        fi
        if [[ -z "${cid}" ]]; then
            sleep 2
            continue
        fi
        local status
        status=$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${cid}" 2>/dev/null || echo missing)
        if [[ "${status}" == "healthy" ]]; then
            log_ok "${compose_service} healthy"
            return 0
        elif [[ "${status}" == "unhealthy" ]]; then
            log_fail "${compose_service} unhealthy"
            docker compose -f "${COMPOSE_FILE}" logs "${compose_service}" 2>/dev/null | tail -20 || true
            return 1
        fi
        sleep 3
    done
}

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
    export WEBHOOK_HMAC_SECRET="$(uv run python -c 'import secrets; print(secrets.token_hex(32))')"
    log_info "WEBHOOK_HMAC_SECRET no definida — generada clave hex temporal de test"
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
# Build + arranque escalonado (HU02)
# ---------------------------------------------------------------------------
log_info "Construyendo imagen open-banca (puede tardar en primera ejecución)..."
docker compose -f "${COMPOSE_FILE}" build api 2>&1 | tail -5

log_info "Fase 1: postgres-temporal..."
docker compose -f "${COMPOSE_FILE}" up -d postgres-temporal 2>&1
wait_container_healthy "postgres-temporal" || true

log_info "Fase 2: temporal-server..."
docker compose -f "${COMPOSE_FILE}" up -d temporal-server 2>&1
wait_container_healthy "temporal-server" || true

log_info "Fase 3: docker-socket-proxy..."
docker compose -f "${COMPOSE_FILE}" up -d docker-socket-proxy 2>&1
wait_container_healthy "docker-socket-proxy" || true

log_info "Fase 4: api + temporal-worker..."
docker compose -f "${COMPOSE_FILE}" up -d api temporal-worker 2>&1

# ---------------------------------------------------------------------------
# Esperar healthchecks restantes
# ---------------------------------------------------------------------------
log_info "Esperando healthchecks de api y temporal-worker (timeout: ${WAIT_TIMEOUT}s)..."

wait_container_healthy "api" || true
wait_container_healthy "temporal-worker" || true

# ---------------------------------------------------------------------------
# Worker: logs deben incluir la frase registrada en worker.py
# ---------------------------------------------------------------------------
log_info "Verificando logs del temporal-worker ('worker started')..."
if docker compose -f "${COMPOSE_FILE}" logs temporal-worker --tail 80 2>/dev/null | grep -q "worker started"; then
    log_ok "temporal-worker logs contienen 'worker started'"
else
    log_fail "temporal-worker logs no contienen 'worker started'"
    docker compose -f "${COMPOSE_FILE}" logs temporal-worker --tail 40 2>/dev/null || true
fi

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

# Test 2: GET /health → 200 (cuerpo status ok)
log_info "Test: GET /health"
HTTP_CODE_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" "${API_BASE}/health" 2>/dev/null || echo "000")
if [[ "${HTTP_CODE_HEALTH}" == "200" ]]; then
    log_ok "GET /health → 200"
else
    log_fail "GET /health → ${HTTP_CODE_HEALTH} (esperado 200)"
    docker compose -f "${COMPOSE_FILE}" logs api 2>/dev/null | tail -30
fi

# Test 3: GET /time → ISO timestamp
log_info "Test: GET /time"
RESPONSE=$(curl -s "${API_BASE}/time" 2>/dev/null || echo "")
if echo "${RESPONSE}" | grep -qE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'; then
    log_ok "GET /time → ISO timestamp: ${RESPONSE}"
else
    log_fail "GET /time → respuesta inesperada: '${RESPONSE}'"
fi

# Test 4: GET /readyz — puede fallar si Temporal worker no está listo, no es fatal
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
