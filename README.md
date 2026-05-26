# open-banca

API self-hosted, open source, para acceder a información financiera de bancos de Panamá vía web scraping asistido por agentes de IA.

> **Por qué existe**: Panamá no tiene Open Banking. Acceder a tus propios datos financieros requiere entrar a la web del banco y descargar reportes manualmente. `open-banca` automatiza ese proceso y lo expone como API. La meta secundaria es **presión institucional** — demostrar que la información que los bancos retienen ya es accesible al usuario, sólo está mal entregada.

## Estado

`v0` — diseño y documentación. Ver [`docs/`](./docs).

## Banco piloto

Banco General (Panamá).

## Quick start — self-hosted (≤30 min)

### Requisitos de host

- Docker Engine 24+ y Docker Compose v2
- Python 3.12+ y [uv](https://docs.astral.sh/uv/)
- Host Linux 64-bit (swap desactivado — ver [checklist M-1..M-7](./docs/05-operations/deployment.md))

### 1. Clonar el repo

```bash
git clone https://github.com/open-banca/open-banca.git
cd open-banca
```

### 2. Configurar variables de entorno

```bash
cp .env.example .env
```

Editar `.env` y completar los valores marcados con `CHANGE_ME`:

| Variable | Descripción | Generar con |
|----------|-------------|-------------|
| `OPEN_BANCA_MASTER_PASSPHRASE` | Cifrado AES-GCM de credenciales | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `WEBHOOK_HMAC_SECRET` | Firma HMAC-SHA256 de webhooks | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `API_KEY` | Autenticación de clientes API | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `TEMPORAL_DB_PASSWORD` | Password de PostgreSQL para Temporal | `python3 -c "import secrets; print(secrets.token_urlsafe(24))"` |
| `ANTHROPIC_API_KEY` | Claude Sonnet (Mapper/Remapper) | Consola Anthropic |

### 3. Construir imágenes

```bash
# Imagen principal (api + worker — mismo target)
docker compose build api

# Imagen sandbox efímero (opcional, para correr scrapes reales)
docker compose build sandbox-runner
```

### 4. Levantar el stack

```bash
# Stack base: api + temporal + worker + postgres
docker compose up -d

# Con Langfuse (trazas LLM):
docker compose --profile langfuse up -d

# Con Temporal UI (http://localhost:8088):
docker compose --profile ui up -d
```

Verificar estado:

```bash
docker compose ps
curl http://localhost:8080/healthz   # → 200 OK
curl http://localhost:8080/readyz    # → 200 OK cuando worker conectado
```

### 5. Registrar credenciales

Opción A — API (recomendado en producción):

```bash
export API_KEY=$(grep ^API_KEY .env | cut -d= -f2)

curl -X POST http://localhost:8080/credentials \
     -H "Authorization: Bearer $API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"bank_id": "banco_general", "username": "TU_USUARIO", "password": "TU_PASSWORD"}'
# → {"credential_ref": "banco_general", ...}  usar en POST /scrape
```

Opción B — CLI interactivo (desarrollo local):

```bash
uv run open-banca register-credentials --bank banco_general
# Almacena username/password cifrados; usa el label del banco como credential_ref
```

### 6. Primer scrape

```bash
curl -X POST http://localhost:8080/scrape \
     -H "Authorization: Bearer $API_KEY" \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: scrape-001" \
     -d '{"bank_id": "banco_general", "credentials": "banco_general", "mode": "full"}'
```

Para detalles completos: [`docs/05-operations/deployment.md`](./docs/05-operations/deployment.md).

### Probar la API (curl, auth, scrape, webhooks)

Guía paso a paso: **[`README-API.md`](./README-API.md)**.

---

## Lectura recomendada

1. [`docs/00-overview.md`](./docs/00-overview.md) — visión, scope, no-goals.
2. [`docs/01-architecture/macro.md`](./docs/01-architecture/macro.md) — arquitectura macro.
3. [`docs/01-architecture/multi-agent.md`](./docs/01-architecture/multi-agent.md) — orquestación de agentes.
4. [`docs/adr/`](./docs/adr/) — decisiones arquitectónicas registradas.

## Desarrollo local — paso a paso

Requiere **tres terminales** abiertas en paralelo.

### Requisitos

- Docker + Docker Compose v2
- Python 3.12+ y [uv](https://docs.astral.sh/uv/)

### Paso 1 — Variables de entorno

```bash
cp .env.example .env
```

Editar `.env` y completar como mínimo estas tres variables:

| Variable | Descripción | Generar con |
|----------|-------------|-------------|
| `OPEN_BANCA_MASTER_PASSPHRASE` | Cifrado AES-GCM de credenciales | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `API_KEY` | Token de autenticación para el API | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `ANTHROPIC_API_KEY` | Claude Sonnet (Mapper/Remapper) | Consola Anthropic |

El resto de variables tienen defaults funcionales para desarrollo local.

### Paso 2 — Dependencias

```bash
uv sync --all-extras
uv run playwright install chromium
```

### Paso 3 — Infraestructura (Terminal 1)

Levanta Temporal + PostgreSQL:

```bash
docker compose -f docker-compose.dev.yml up -d
```

Servicios expuestos:

| Servicio | Puerto |
|----------|--------|
| Temporal gRPC | `7233` |
| Temporal Web UI | `http://localhost:8088` |

Verificar que Temporal esté listo:

```bash
docker compose -f docker-compose.dev.yml logs -f temporal-server
# Esperar línea: "... all services are ready"
```

### Paso 4 — Worker Temporal (Terminal 2)

```bash
uv run python -m open_banca_orchestrator.worker
```

Salida esperada:
```
Worker started — task queue: open-banca-task-queue
```

### Paso 5 — API (Terminal 3)

```bash
uv run uvicorn open_banca_api.main:app --reload --port 8000
```

> **Producción (Docker):** el servicio `api` expone puerto **8080** con
> `uvicorn open_banca_api.main:app --host 0.0.0.0 --port 8080`.

### Paso 6 — Verificar

```bash
export API_KEY=$(grep ^API_KEY .env | cut -d= -f2)

# Health check (sin auth)
curl http://localhost:8000/healthz

# Listar bancos soportados (requiere auth)
curl -H "Authorization: Bearer $API_KEY" http://localhost:8000/banks

# Endpoint autenticado de prueba
curl -H "Authorization: Bearer $API_KEY" http://localhost:8000/accounts
```

Documentación interactiva: `http://localhost:8000/docs`

Para bajar el stack de infra:

```bash
docker compose -f docker-compose.dev.yml down
```

### Tests

```bash
# Suite completa (no requiere Docker — usa time-skipping env de Temporal)
uv run pytest packages/adapters/orchestrator -v

# Solo unit tests
uv run pytest -m "not live"

# Lint + tipos
uv run ruff check packages/adapters/orchestrator
uv run pyrefly check packages/adapters/orchestrator
```

## Licencia

AGPL-3.0 — cualquier servicio derivado debe permanecer abierto. Es deliberado.
