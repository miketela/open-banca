# Guía para probar la API — open-banca

Guía práctica para levantar la API, autenticarte y ejecutar un flujo de prueba (scrape, jobs, webhooks). Para arquitectura del contrato HTTP ver [`docs/02-components/api.md`](./docs/02-components/api.md).

## Requisitos

- Python 3.12+ y [uv](https://docs.astral.sh/uv/)
- Docker + Docker Compose v2 (recomendado para scrape real con Temporal)
- Claves en `.env` (ver abajo)

```bash
git clone https://github.com/miketela/open-banca.git
cd open-banca
uv sync --all-packages
```

---

## 1. Configurar secretos

```bash
./scripts/bootstrap_env.sh   # crea .env desde .env.example (no sobrescribe .env existente)
```

Edita `.env` y completa al menos:

| Variable | Uso |
|----------|-----|
| `API_KEY` | Token que usarás en `Authorization: Bearer …` |
| `OPEN_BANCA_API_TOKEN` | **Debe coincidir con `API_KEY`** — la app FastAPI lee esta variable |
| `OPEN_BANCA_MASTER_PASSPHRASE` | Cifrado del vault SQLite |
| `WEBHOOK_HMAC_SECRET` | Firma HMAC de webhooks (hex recomendado) |
| `TEMPORAL_DB_PASSWORD` | Postgres de Temporal |
| `ANTHROPIC_API_KEY` | Worker / Mapper (scrape real) |

Ejemplo para alinear auth en `.env`:

```bash
API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
OPEN_BANCA_API_TOKEN="${API_KEY}"
```

Carga variables en tu shell:

```bash
set -a && source .env && set +a
export API_BASE="http://localhost:${OPEN_BANCA_API_PORT:-8080}"
export AUTH_HEADER="Authorization: Bearer ${OPEN_BANCA_API_TOKEN:-${API_KEY}}"
```

---

## 2. Levantar el stack (recomendado)

Arranque escalonado (evita carreras entre Postgres y Temporal):

```bash
docker compose build api
docker compose up -d postgres-temporal
# esperar healthy: docker compose ps postgres-temporal
docker compose up -d temporal-server docker-socket-proxy
docker compose up -d api temporal-worker
```

Smoke automatizado (opcional):

```bash
bash scripts/smoke_compose.sh
# dejar el stack arriba para probar manualmente:
KEEP_UP=1 bash scripts/smoke_compose.sh
```

Comprobar que la API responde:

```bash
curl -fsS "${API_BASE}/health" | python3 -m json.tool
# → {"status":"ok","version":"..."}

curl -fsS "${API_BASE}/time" | python3 -m json.tool
# → hora UTC (sin auth)
```

Documentación interactiva (Swagger):

```text
${API_BASE}/docs
${API_BASE}/redoc
```

> **Nota:** `docker-compose.yml` documenta `GET /healthz` y `GET /readyz` para probes; en la imagen actual el endpoint implementado de liveness es **`GET /health`**. Usa `/health` para pruebas manuales.

Temporal UI (perfil opcional):

```bash
docker compose --profile ui up -d temporal-ui
# http://localhost:${TEMPORAL_UI_PORT:-8088}
```

---

## 3. Autenticación

Todos los endpoints excepto **`GET /health`** y **`GET /time`** exigen:

```http
Authorization: Bearer <OPEN_BANCA_API_TOKEN>
```

Ejemplo:

```bash
curl -s "${API_BASE}/banks" -H "${AUTH_HEADER}" | python3 -m json.tool
```

Sin token o token incorrecto → `401 Unauthorized`.

---

## 4. Registrar credenciales (CLI, no HTTP)

Las credenciales bancarias **no** se envían por REST; van al vault cifrado vía CLI:

```bash
export OPEN_BANCA_MASTER_PASSPHRASE="$(grep '^OPEN_BANCA_MASTER_PASSPHRASE=' .env | cut -d= -f2-)"

uv run open-banca register-credentials --bank banco_general --label personal
uv run open-banca list-credentials --bank banco_general
```

En `POST /scrape`, el campo `credentials` es una **referencia opaca** al set registrado (p. ej. la etiqueta `personal`).

---

## 5. Endpoints útiles para pruebas

| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| `GET` | `/health` | No | Liveness (`status: ok`) |
| `GET` | `/time` | No | Hora UTC (diagnóstico reloj / webhooks) |
| `GET` | `/banks` | Sí | Bancos soportados (`banco_general`, …) |
| `GET` | `/accounts` | Sí | Cuentas cacheadas (`?bank=banco_general`) |
| `POST` | `/scrape` | Sí | Crear job de scrape → `202` + `job_id` |
| `GET` | `/jobs/{id}` | Sí | Estado del job |
| `GET` | `/jobs/{id}/result` | Sí | Resultado canónico (solo si `completed`) |
| `GET` | `/jobs/{id}/cursor` | Sí | Cursors incrementales por cuenta |
| `POST` | `/jobs/{id}/otp-confirmed` | Sí | Confirmar OTP (push Clave Móvil) → `204` |
| `POST` | `/jobs/{id}/human-input` | Sí | Respuesta a pregunta de seguridad → `204` |
| `POST` | `/jobs/{id}/cancel` | Sí | Cancelar job → `204` |
| `POST` | `/webhooks/test` | Sí | Webhook de prueba firmado |
| `GET` | `/webhooks/dlq` | Sí | Cola muerta de webhooks |
| `POST` | `/maps/{bank}/proposals/{id}/approve` | Sí | Aprobar propuesta de remap (HITL) |

OpenAPI completo: **`${API_BASE}/openapi.json`**.

---

## 6. Flujo de prueba: scrape completo

### 6.1 Crear job

`POST /scrape` requiere header **`Idempotency-Key`** (recomendado) y cuerpo JSON:

```bash
IDEM_KEY="scrape-test-$(date +%s)"

RESP=$(curl -s -w "\n%{http_code}" -X POST "${API_BASE}/scrape" \
  -H "${AUTH_HEADER}" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: ${IDEM_KEY}" \
  -d '{
    "bank_id": "banco_general",
    "credentials": "personal",
    "mode": "full",
    "accounts": [],
    "webhook_url": null,
    "metadata": {"source": "manual-test"}
  }')

HTTP_BODY=$(echo "$RESP" | head -n -1)
HTTP_CODE=$(echo "$RESP" | tail -n 1)
echo "HTTP ${HTTP_CODE}"
echo "$HTTP_BODY" | python3 -m json.tool

JOB_ID=$(echo "$HTTP_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
echo "JOB_ID=${JOB_ID}"
```

- **`mode`**: `"full"` (histórico ~6 meses) o `"incremental"` (desde cursor guardado).
- **`accounts`**: lista de IDs; `[]` = todas las cuentas.
- Respuesta esperada: **`202 Accepted`** con `job_id` y `status` inicial (`pending`).

Repetir la misma `Idempotency-Key` + mismo body → mismo `job_id`. Misma key con body distinto → `409`.

### 6.2 Polling de estado

```bash
watch -n 5 "curl -s '${API_BASE}/jobs/${JOB_ID}' -H '${AUTH_HEADER}' | python3 -m json.tool"
```

Estados típicos: `pending` → `running` → `otp_required` | `human_input_required` → `completed` | `failed`.

### 6.3 OTP (Banco General — Clave Móvil)

Cuando `status` sea `otp_required`, acepta el push en el dispositivo y luego:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  "${API_BASE}/jobs/${JOB_ID}/otp-confirmed" \
  -H "${AUTH_HEADER}"
# esperado: 204
```

### 6.4 Pregunta de seguridad (si aplica)

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  "${API_BASE}/jobs/${JOB_ID}/human-input" \
  -H "${AUTH_HEADER}" \
  -H "Content-Type: application/json" \
  -d '{"field_key": "security_q_mother_color", "answer": "rojo", "persist": true}'
# esperado: 204 (solo si status=human_input_required)
```

### 6.5 Resultado

```bash
curl -s "${API_BASE}/jobs/${JOB_ID}/result" -H "${AUTH_HEADER}" | python3 -m json.tool
```

Solo responde `200` cuando el job está **`completed`**; si no, `409 job_not_completed`.

### 6.6 Cancelar

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  "${API_BASE}/jobs/${JOB_ID}/cancel" \
  -H "${AUTH_HEADER}"
# esperado: 204
```

---

## 7. Webhooks de prueba

Configura un receptor (p. ej. [webhook.site](https://webhook.site)) y exporta:

```bash
export OPEN_BANCA_WEBHOOK_TARGET_URL="https://webhook.site/<tu-uuid>"
export OPEN_BANCA_WEBHOOK_SECRET="${WEBHOOK_HMAC_SECRET}"
```

Dispara un evento sintético:

```bash
curl -s -X POST "${API_BASE}/webhooks/test" \
  -H "${AUTH_HEADER}" \
  -H "Content-Type: application/json" \
  -d '{"event_type": "job.completed"}' | python3 -m json.tool
```

En el receptor, verifica el header **`X-OpenBanca-Signature`** (HMAC-SHA256 con `WEBHOOK_HMAC_SECRET`).

Listar entregas fallidas:

```bash
curl -s "${API_BASE}/webhooks/dlq" -H "${AUTH_HEADER}" | python3 -m json.tool
```

---

## 8. Solo API en local (sin Docker)

Útil para probar validación, auth y OpenAPI **sin** workflow Temporal (el scrape devolverá `503` si no hay worker).

```bash
export OPEN_BANCA_API_TOKEN="${API_KEY:-dev-insecure-token}"
export OPEN_BANCA_DB_PATH="${PWD}/data/open-banca-dev.db"
export OPEN_BANCA_MASTER_PASSPHRASE="${OPEN_BANCA_MASTER_PASSPHRASE:-dev-passphrase-change-me}"

mkdir -p data
uv run uvicorn open_banca_api.main:app --host 127.0.0.1 --port 8080 --reload
```

En otra terminal:

```bash
export API_BASE=http://127.0.0.1:8080
export AUTH_HEADER="Authorization: Bearer ${OPEN_BANCA_API_TOKEN}"

curl -s "${API_BASE}/health"
curl -s "${API_BASE}/banks" -H "${AUTH_HEADER}"
```

Para scrape end-to-end necesitas **Temporal + worker** (`docker compose` o `docker compose -f docker-compose.dev.yml` + `uv run python -m open_banca_orchestrator.worker`).

---

## 9. Tests automatizados de la API

```bash
# Tests del adaptador API (sin live)
uv run pytest packages/adapters/api -m "not live" -q

# Contrato OpenAPI / auth / scrape validation
uv run pytest packages/adapters/api/tests/test_scrape.py packages/adapters/api/tests/test_auth.py -v
```

---

## 10. Problemas frecuentes

| Síntoma | Causa probable | Qué hacer |
|---------|----------------|-----------|
| `401` en todos los endpoints | `OPEN_BANCA_API_TOKEN` ≠ token del header | Igualar `OPEN_BANCA_API_TOKEN` y `API_KEY` en `.env` |
| `503 workflow_start_failed` | Worker o Temporal caído | `docker compose ps`, logs de `temporal-worker` |
| `409 job_not_completed` en `/result` | Job aún en curso o falló | `GET /jobs/{id}` y logs del worker |
| `409 job_not_awaiting_otp` | OTP confirmado demasiado pronto/tarde | Repetir cuando `status=otp_required` |
| Puerto incorrecto en curl | Solo cambiaste `OPEN_BANCA_API_PORT` | Usar `${OPEN_BANCA_API_PORT:-8080}` en `API_BASE` |
| Credenciales inválidas en scrape | Vault vacío o label incorrecto | `register-credentials` + usar mismo label en `credentials` |

Logs útiles:

```bash
docker compose logs api --tail 100
docker compose logs temporal-worker --tail 100 | grep -E "worker started|ERROR"
```

---

## Referencias

- Despliegue: [`docs/05-operations/deployment.md`](./docs/05-operations/deployment.md)
- Piloto Banco General: [`packages/banks/banco_general/PILOT_RUNBOOK.md`](./packages/banks/banco_general/PILOT_RUNBOOK.md)
- Smoke E2E (HU03): [`specs/dev-specs/HU03-smoke-e2e-banco-general.md`](./specs/dev-specs/HU03-smoke-e2e-banco-general.md)
