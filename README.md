# open-banca

API self-hosted, open source, para acceder a información financiera de bancos de Panamá vía web scraping asistido por agentes de IA.

> **Por qué existe**: Panamá no tiene Open Banking. Acceder a tus propios datos financieros requiere entrar a la web del banco y descargar reportes manualmente. `open-banca` automatiza ese proceso y lo expone como API. La meta secundaria es **presión institucional** — demostrar que la información que los bancos retienen ya es accesible al usuario, sólo está mal entregada.

## Estado

`v0` — diseño y documentación. Ver [`docs/`](./docs).

## Banco piloto

Banco General (Panamá).

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
