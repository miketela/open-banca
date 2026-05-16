# HU05 — USER-TEST 2: Scraper runner E2E (task 13)

> Issue: TBD
> Branch: TBD
> Estado: draft
> Depende de: taskmaster:11, taskmaster:12

## Contexto

Segundo checkpoint. Valida que el Scraper Runner Playwright (9 step types, 0 LLM) ejecuta correctamente un `map.json` end-to-end usando HAR record/replay como driver de tests. Esto prueba el camino "happy path" del runner sin involucrar LLM ni banco real.

## Acceptance Criteria

- [ ] Runner ejecuta los 9 step types contra fixtures HAR sin error.
- [ ] `ScrapeJobWorkflow` corre en Temporal local end-to-end con fixture (login → nav → download → parse → validate → emit webhook).
- [ ] HAR record/replay determinista: misma corrida = mismo resultado.
- [ ] BreakageEvent se emite cuando un step falla (test con HAR corrupto).
- [ ] Webhook `job.completed` se emite y firma HMAC verifica.

## Plan técnico

```bash
# Tests runner por step type
uv run pytest packages/adapters/browser/tests/ -k "runner"

# Workflow E2E con HAR fixture
uv run pytest packages/adapters/orchestrator/tests/test_scrape_job_workflow.py

# Replay determinismo
uv run pytest packages/adapters/orchestrator/tests/test_har_replay.py -v

# BreakageEvent en step fail
uv run pytest -k "breakage"
```

Si algún test falla, debug y arreglar antes de marcar el checkpoint.

## Tests

- Unit tests por step type.
- Integration test del workflow completo con HAR.
- Replay determinism test.

## Riesgos

- **Tests dependen del trabajo F1 del plan** (workflow gaps cerrados). Si HU está antes que F1 termine, falla. Validar en orden.

## Definition of Done

- AC todos checked.
- `task-master set-status --id=13 --status=done`.
- Evidencia: output del workflow E2E test en la issue.
