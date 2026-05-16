# HU04 — USER-TEST 1: Foundation review (task 8)

> Issue: #10
> Branch: TBD
> Estado: draft
> Depende de: taskmaster:3, taskmaster:4, taskmaster:5, taskmaster:6, taskmaster:7

## Contexto

Primer USER-TEST checkpoint per PRD §USER-TESTs. Valida que la fundación (monorepo + hexagonal scaffolding + FastAPI skeleton + storage + secret vault + Temporal + CI) está sólida antes de empezar Phase 2 (scraper runner). Todos los tasks 1-7 están done en Taskmaster pero el checkpoint nunca fue marcado.

## Acceptance Criteria

- [ ] `uv sync --all-packages` corre limpio.
- [ ] `uv run ruff check .` pasa sin errores.
- [ ] `uv run pyrefly check` pasa sin errores.
- [ ] `uv run pytest -m "not live"` pasa con 0 failed.
- [ ] Canary redact CI test pasa (`uv run pytest -m canary`).
- [ ] FastAPI `/health` responde 200 con `uv run uvicorn adapters.api.main:app`.
- [ ] Temporal worker arranca y queda esperando tasks (`uv run python -m adapters.orchestrator.worker`).
- [ ] Secret vault: round-trip de credenciales vía CLI `register-credentials` → `list-credentials` funciona.
- [ ] CI pipeline en GitHub Actions verde en la branch del último commit.

## Plan técnico

Manual checklist run. Comandos exactos:

```bash
uv sync --all-packages
uv run ruff check . && uv run ruff format --check .
uv run pyrefly check
uv run pytest -m "not live" --tb=short
uv run pytest -m canary
docker compose -f docker-compose.dev.yml up -d
uv run uvicorn adapters.api.main:app --reload &
curl -fsS http://localhost:8000/health
uv run python -m adapters.orchestrator.worker &
uv run open-banca register-credentials --bank test_dummy
uv run open-banca list-credentials
```

## Tests

Todos los comandos arriba deben retornar exit 0 o output esperado.

## Riesgos

- **CI verde local pero falla en GitHub Actions**: validar también en el runner CI real.
- **Tests rojos del plan F2 todavía sin arreglar**: este USER-TEST requiere suite verde, así que HU04 depende implícitamente de Fase F2 del plan en vuelo.

## Definition of Done

- AC todos checked.
- `task-master set-status --id=8 --status=done`.
- Comentario en la issue con outputs de los comandos clave + screenshot CI verde.
