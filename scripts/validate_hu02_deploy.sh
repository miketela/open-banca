#!/usr/bin/env bash
# scripts/validate_hu02_deploy.sh — HU02 deploy validation (stack must be running)
#
# Verifica los 5 servicios base del stack producción, healthchecks Docker,
# y endpoints GET /healthz + GET /readyz en la API.
#
# Uso:
#   cp .env.example .env   # completar secrets reales
#   docker compose build api sandbox-runner
#   docker compose up -d
#   bash scripts/validate_hu02_deploy.sh
#
# Variables opcionales:
#   API_PORT       Puerto API (default: 8080)
#   COMPOSE_FILE   Compose file (default: docker-compose.yml)
#   STRICT_READYZ  Si "1", falla si /readyz != 200 (default: 0)

set -euo pipefail

API_PORT="${API_PORT:-8080}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
STRICT_READYZ="${STRICT_READYZ:-0}"
API_BASE="http://localhost:${API_PORT}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0

log_info() { echo -e "${YELLOW}[INFO]${NC} $*"; }
log_ok()   { echo -e "${GREEN}[PASS]${NC} $*"; PASS=$((PASS + 1)); }
log_fail() { echo -e "${RED}[FAIL]${NC} $*"; FAIL=$((FAIL + 1)); }

# 5 servicios base HU02 (nombres de container en docker-compose.yml)
REQUIRED_CONTAINERS=(
  "open-banca-docker-socket-proxy"
  "open-banca-postgres-temporal"
  "open-banca-temporal"
  "open-banca-api"
  "open-banca-temporal-worker"
)

# Servicios con healthcheck explícito (worker usa stub — solo running)
HEALTHCHECK_CONTAINERS=(
  "open-banca-docker-socket-proxy"
  "open-banca-postgres-temporal"
  "open-banca-temporal"
  "open-banca-api"
)

if ! command -v docker &>/dev/null; then
  log_fail "docker no disponible"
  exit 1
fi

if ! docker info &>/dev/null; then
  log_fail "Docker daemon no disponible"
  exit 1
fi

log_info "Validando docker compose config..."
if docker compose -f "${COMPOSE_FILE}" config --quiet 2>&1; then
  log_ok "docker compose config --quiet"
else
  log_fail "docker compose config --quiet"
  exit 1
fi

log_info "Verificando contenedores requeridos..."
for C in "${REQUIRED_CONTAINERS[@]}"; do
  if docker inspect "${C}" &>/dev/null; then
    STATE=$(docker inspect --format='{{.State.Status}}' "${C}")
    if [[ "${STATE}" == "running" ]]; then
      log_ok "${C} running"
    else
      log_fail "${C} estado=${STATE} (esperado running)"
    fi
  else
    log_fail "${C} no existe — ejecute: docker compose up -d"
  fi
done

log_info "Verificando healthchecks Docker..."
for C in "${HEALTHCHECK_CONTAINERS[@]}"; do
  if ! docker inspect "${C}" &>/dev/null; then
    continue
  fi
  HEALTH=$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${C}")
  case "${HEALTH}" in
    healthy)
      log_ok "${C} healthy"
      ;;
    starting)
      log_fail "${C} health=starting (espere y reintente)"
      ;;
    unhealthy)
      log_fail "${C} unhealthy"
      docker compose -f "${COMPOSE_FILE}" logs "${C}" 2>/dev/null | tail -15 || true
      ;;
    none)
      log_info "${C} sin healthcheck (omitido)"
      ;;
    *)
      log_fail "${C} health=${HEALTH}"
      ;;
  esac
done

log_info "GET ${API_BASE}/healthz"
HTTP_HEALTHZ=$(curl -s -o /dev/null -w "%{http_code}" "${API_BASE}/healthz" 2>/dev/null || echo "000")
if [[ "${HTTP_HEALTHZ}" == "200" ]]; then
  log_ok "GET /healthz → 200"
else
  log_fail "GET /healthz → ${HTTP_HEALTHZ} (esperado 200)"
fi

log_info "GET ${API_BASE}/readyz"
HTTP_READYZ=$(curl -s -o /dev/null -w "%{http_code}" "${API_BASE}/readyz" 2>/dev/null || echo "000")
if [[ "${HTTP_READYZ}" == "200" ]]; then
  log_ok "GET /readyz → 200 (Temporal + storage listos)"
elif [[ "${STRICT_READYZ}" == "1" ]]; then
  log_fail "GET /readyz → ${HTTP_READYZ} (STRICT_READYZ=1)"
else
  log_info "GET /readyz → ${HTTP_READYZ} (informativo; worker puede estar conectando)"
fi

log_info "Worker logs (grep worker started)..."
if docker compose -f "${COMPOSE_FILE}" logs temporal-worker --no-color 2>/dev/null | tail -50 | grep -qi "worker started"; then
  log_ok "temporal-worker: 'worker started' en logs"
else
  log_info "temporal-worker: 'worker started' no encontrado en últimas 50 líneas (revisar manualmente)"
fi

echo ""
echo "========================================"
echo -e "HU02 validation: ${GREEN}${PASS} PASS${NC} / ${RED}${FAIL} FAIL${NC}"
echo "========================================"

[[ "${FAIL}" -eq 0 ]]
