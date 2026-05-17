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

### 5. Registrar primer banco

```bash
# Registrar credenciales de Banco General (interactivo)
curl -X POST http://localhost:8080/banks \
     -H "X-API-Key: $(grep ^API_KEY .env | cut -d= -f2)" \
     -H "Content-Type: application/json" \
     -d '{"bank_id": "banco_general"}'

# Registrar credenciales bancarias
curl -X POST http://localhost:8080/credentials \
     -H "X-API-Key: $(grep ^API_KEY .env | cut -d= -f2)" \
     -H "Content-Type: application/json" \
     -d '{"bank_id": "banco_general", "username": "TU_USUARIO", "password": "TU_PASSWORD"}'
```

### 6. Primer scrape

```bash
curl -X POST http://localhost:8080/scrape \
     -H "X-API-Key: $(grep ^API_KEY .env | cut -d= -f2)" \
     -H "Content-Type: application/json" \
     -d '{"bank_id": "banco_general"}'
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

## Desarrollo local

### Requisitos

- Docker + Docker Compose v2
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) como package manager

### Setup inicial

```bash
uv sync --all-extras
```

### Levantar stack de desarrollo (Temporal)

```bash
docker compose -f docker-compose.dev.yml up -d
```

Servicios expuestos:

| Servicio | Puerto | Descripción |
|----------|--------|-------------|
| Temporal gRPC | `7233` | SDK de Python + workers |
| Temporal Web UI | `8080` | `http://localhost:8080` |

Para bajar el stack:

```bash
docker compose -f docker-compose.dev.yml down
```

Para ver logs:

```bash
docker compose -f docker-compose.dev.yml logs -f temporal-server
```

### Correr el worker (en el host)

```bash
# Con defaults (apunta a localhost:7233)
uv run python -m open_banca_orchestrator.worker

# Con variables de entorno
OPEN_BANCA_TEMPORAL_ADDRESS=localhost:7233 \
OPEN_BANCA_TEMPORAL_TASK_QUEUE=open-banca-task-queue \
uv run python -m open_banca_orchestrator.worker
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
