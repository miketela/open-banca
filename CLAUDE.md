# Project: open-banca

> **Taskmaster-Managed | TDD-First | AGPL-3.0**

## Project Overview

API REST self-hosted, open source AGPL-3.0, que automatiza la captura de datos bancarios de Panamá usando agentes de IA + Playwright + Temporal. Banco piloto: **Banco General**. Goal secundario: presión institucional sobre bancos PA — la licencia AGPL fuerza que cualquier servicio derivado quede abierto.

**Key Documents:**
- PRD: `.taskmaster/docs/prd.txt` (100% EXCELLENT, 13/13 checks)
- Tasks: `.taskmaster/tasks/tasks.json` (30 tasks, 5 USER-TEST checkpoints)
- Architecture: `docs/01-architecture/`
- ADRs: `docs/adr/` (formato Nygard)
- Decisiones consolidadas: `docs/DECISIONS.md`
- Banco piloto specs: `docs/06-banks/banco-general.md`

**Idioma:** documentación en español, tecnicismos en inglés.

---

## Development Philosophy

> "Planning is 95% of the work. Tests are 95% of planning. Write tests first, implement second, validate always."

**MANDATORY: TDD (Red → Green → Refactor)** para cada task del roadmap.

---

## Tech Stack

- **Lenguaje**: Python 3.12+
- **Package manager**: uv (workspaces)
- **Lint/format**: Ruff
- **Type check**: Pyrefly
- **Monorepo**: Nx + `@nxlv/python`
- **API**: FastAPI + Pydantic v2
- **Orquestación**: Temporal (Python SDK)
- **Browser automation**: browser-use (libreria local en `/Users/mike/Documents/maybe finance/browser-use`) + Playwright
- **LLM**: PydanticAI + LiteLLM (Claude Sonnet 4.6 vision para Mapper/Remapper, DeepSeek V3 para Validator/Judge)
- **Storage**: SQLite + sqlcipher (cifrada at-rest, Argon2id KDF + AES-GCM)
- **Excel parsing**: openpyxl + DSL whitelisted helpers
- **Sandbox**: Docker container efímero per-job
- **Observabilidad**: Langfuse self-hosted (opcional, profile-gated) + OpenTelemetry
- **Webhooks**: HMAC-SHA256, retry exponential, DLQ
- **Testing**: pytest + Temporal time-skipping + HAR record/replay + PydanticAI test models

## Architecture Overview

**Hexagonal (ports & adapters):**

```
domain/         (entities + ports puros)
application/    (use cases)
adapters/
  ├── api/           (FastAPI)
  ├── orchestrator/  (Temporal workflows + activities)
  ├── browser/       (browser-use Mapper/Remapper + Playwright Runner)
  ├── llm/           (PydanticAI + LiteLLM)
  ├── storage/       (SQLite + sqlcipher)
  ├── parsing/       (Excel DSL engine)
  └── sandbox/       (Docker per-job)
banks/banco_general/ (map.json + parser.json + fixtures HAR + tests)
shared/schemas/      (Pydantic cross-package)
```

**5 agentes IA**: Mapper (Claude vision) → Scraper Runner (sin LLM) → Validator (DeepSeek) → Judge (DeepSeek vision) → Remapper (Claude vision).

**Mapper-Runner split**: agente Mapper genera `map.json` declarativo. Scraper Runner (Playwright puro, 0 LLM) lo ejecuta cada corrida. Reduce costo y blast radius.

## Key Dependencies

- `fastapi`, `pydantic>=2`, `pydantic-ai`, `litellm`, `temporalio`, `playwright`, `browser-use` (path dep al directorio hermano), `openpyxl`, `pysqlcipher3`, `argon2-cffi`, `cryptography`, `httpx`, `slowapi`, `opentelemetry-sdk`, `cosign` (CLI para community maps).

## Testing Framework

- pytest + pytest-asyncio
- Temporal time-skipping (`temporalio.testing`)
- Playwright HAR record/replay (fixtures por banco)
- PydanticAI test models (no gasto de tokens en CI)
- Property-based: `hypothesis`
- Smoke contra banco real: opt-in via env var (`OPEN_BANCA_LIVE_SMOKE=1`), creds del operador

## Development Environment

```bash
# Setup
uv sync --all-packages
docker compose -f docker-compose.dev.yml up -d  # Temporal + (opcional) Langfuse
uv run playwright install chromium

# Worker Temporal
uv run python -m adapters.orchestrator.worker

# API local
uv run uvicorn adapters.api.main:app --reload
```

---

## Taskmaster Workflow

```bash
# Ver siguiente task disponible
task-master next

# Detalle
task-master show <id>

# Iniciar
task-master set-status --id=<id> --status=in-progress

# Completar (solo después de validation)
task-master set-status --id=<id> --status=done
```

**5 USER-TEST checkpoints** en tasks 8, 13, 18, 22, 29. PARAR ahí para validación humana antes de continuar siguiente fase.

## TDD Cycle por Task

1. **RED** — Leer task + PRD §correspondiente. Escribir tests basados en AC. Run: deben fallar.
2. **GREEN** — Implementar mínimo para pasar tests.
3. **REFACTOR** — Mejorar sin romper tests.
4. **VALIDATE** — Verificar AC del task, ejecutar canary redact si aplica, blind-validator opcional.
5. **COMMIT** — Tests pass + AC met → mark done.

## Test Command

```bash
uv run pytest                                  # full suite
uv run pytest -m "not live"                    # skip live smoke
uv run pytest tests/unit/                      # unit only
uv run pytest --cov=src --cov-report=term      # coverage
```

---

## Security Critical Rules

1. **Credentials NUNCA en logs/Langfuse/OTel/HAR.** Filter middleware redact obligatorio. Canary CI test (`SECRET_CANARY_VALUE`) corre en cada PR.
2. **Mapper/Remapper usan `sensitive_data` de browser-use.** LLM nunca recibe plaintext de creds. Fuzz test instrumenta LiteLLM y verifica.
3. **Sandbox Docker per-job.** Network allowlist (dominio banco + APIs LLM). Caps drop, read-only rootfs, non-root, resource limits. Container destruido al terminar job.
4. **Excel parser DSL = NO Python arbitrario.** AST whitelist rechaza `eval/exec/import`.
5. **Maps community firmados con cosign keyless GitHub OIDC.** Maps sin firma → runtime rechaza.
6. **Master passphrase NO en config.** Solo env. Argon2id + AES-GCM cifra todo at-rest en sqlcipher DB.
7. **Cost guardrails enforce centralmente.** Max $0.50 LLM/job, 3 remap/24h por banco, circuit breaker login fallidos.

## Out of Scope (v1)

- Préstamos hipotecarios / personales (v2)
- Inversiones, fideicomisos (v2)
- Captcha resolving (decisión postergada)
- UI / frontend (solo API)
- Multi-tenant SaaS (es self-hosted single-org)

---

## Quick Reference

### Daily

```bash
task-master next
task-master show <id>
# leer PRD §relevante + docs/02-components/<componente>.md
# escribir tests (RED)
uv run pytest tests/unit/test_<feature>.py  # debe fallar
# implementar (GREEN)
uv run pytest
# refactor + validate
git commit -m "feat(task-<id>): <desc>"
task-master set-status --id=<id> --status=done
```

### Common

```bash
# Taskmaster
task-master list                          # todas
task-master next                          # siguiente disponible
task-master show <id>
task-master set-status --id=<id> --status=done

# Lint + types
uv run ruff check . && uv run ruff format .
uv run pyrefly check

# Tests
uv run pytest
uv run pytest -m "not live"

# Temporal
docker compose -f docker-compose.dev.yml up -d
uv run python -m adapters.orchestrator.worker
```

### Documentación canónica (verificar antes de tocar)

1. `docs/00-overview.md` — visión, scope, no-goals, stack
2. `docs/DECISIONS.md` — registro completo de decisiones
3. `docs/adr/*.md` — ADRs Nygard (Context, Decision, Consequences, Alternatives)
4. `docs/01-architecture/*.md` — macro, hexagonal, multi-agent, data-flow
5. `docs/02-components/*.md` — un md por componente
6. `docs/03-flows/*.md` — flujos end-to-end con sequence diagrams
7. `docs/04-security/*.md` — threat model, sandbox, community maps, secrets
8. `docs/05-operations/*.md` — deployment, observability, cost guardrails, testing
9. `docs/06-banks/banco-general.md` — banco piloto

---

## Conventions

- Diagramas en Mermaid (embedidos en .md, comentarios en español)
- Texto en español; tecnicismos canónicos en inglés
- ADRs cierran con `Status: Accepted (YYYY-MM-DD)`
- Docs no contienen clases ni código Python concreto — solo nombres conceptuales y relaciones
- Commits: conventional commits, fragmento del task ID (`feat(task-12): ...`)
- Branch por task: `task-{id}-{slug}`. Merge a main con tag checkpoint en USER-TEST.

---

**License:** AGPL-3.0. Forks o servicios derivados deben publicar su código.
