# Roadmap

> Roadmap task-level basado en Taskmaster. **Source of truth**: `.taskmaster/tasks/tasks.json` (consultar con `task-master list`). Esta vista es snapshot ejecutivo, no se sincroniza automáticamente.
>
> **Estado snapshot**: 29/36 done (81%) al 2026-05-16. Pendientes: los 6 USER-TEST checkpoints + task 30 (v1.0.0 release).

## Convenciones

- `[x]` = done. `[ ]` = pending. `[~]` = pending pero pre-trabajo hecho (USER-TEST checkpoints requieren validación humana, no código).
- `→` indica dependencias.
- **USER-TEST checkpoint** = parar para validación humana antes de continuar siguiente fase.

## Fase 1 — Foundation (tasks 1-8)

Objetivo: monorepo + scaffolding hexagonal + CI verde + secret vault funcional.

- [x] **1** Setup monorepo Nx + Python uv workspaces
- [x] **2** Hexagonal scaffolding: domain + application layers → 1
- [x] **3** FastAPI skeleton: 10 endpoints stub + OpenAPI 3.1 → 2
- [x] **4** Storage adapter SQLite + sqlcipher → 2
- [x] **5** Secret vault: Argon2id + AES-GCM + filter middleware → 4
- [x] **6** Temporal local setup + worker scaffolding → 1
- [x] **7** CI pipeline: ruff + pyrefly + pytest + canary redact → 1, 5
- [~] **8** **USER-TEST checkpoint 1**: Foundation review → 3, 4, 5, 6, 7

## Fase 2 — Scraper runner E2E (tasks 9-13)

Objetivo: Runner Playwright determinístico + workflow Temporal + HAR replay infrastructure.

- [x] **9** Scraper runner Playwright: 9 step types (sin LLM) → 2, 6
- [x] **10** BreakageEvent contract + emit on step failure → 9
- [x] **11** HAR record/replay testing infrastructure → 9
- [x] **12** ScrapeJobWorkflow + 11 activities skeleton → 6, 9, 10
- [~] **13** **USER-TEST checkpoint 2**: Scraper runner E2E → 11, 12

## Fase 3 — Banco General piloto (tasks 14-18, 32)

Objetivo: primer banco productivo. Mapper genera map.json. Parser DSL valida Excel real.

- [x] **14** Mapper agent: browser-use + Claude vision → 12
- [x] **15** Excel parser DSL engine + helpers whitelisted → 2
- [x] **16** Banco General: primer mapping run + map.json + parser.json → 14, 15
- [x] **17** Schema canónico unificado + 3-niveles dedup → 4, 15
- [x] **32** Incremental scrape: cursor `since` storage + API params + buffer logic → 16, 17
- [~] **18** **USER-TEST checkpoint 3**: Banco General piloto → 16, 17

## Fase 4 — Self-healing (tasks 19-22, 36)

Objetivo: Validator/Judge/Remapper + HITL approval flow + human-input para preguntas de seguridad.

- [x] **19** Validator + Judge agents (PydanticAI + DeepSeek) → 10, 12
- [x] **20** Remapper agent + dry-run validation → 14, 19
- [x] **21** Proposal endpoints + TTL + git apply (HITL flow) → 20
- [x] **36** `prompt_user` step + human-input flow ([ADR-0021](../docs/adr/0021-prompt-user-step.md)) → 9, 11, 12, 5, 25
- [~] **22** **USER-TEST checkpoint 4**: Self-healing flow → 21

## Fase 5 — Operations + security (tasks 23-29, 31, 33, 34)

Objetivo: production hardening — sandbox, webhooks, cost guardrails, observabilidad, CLI, compose full stack.

- [x] **23** Sandbox Docker per-job + network policies → 12
- [x] **24** docker-socket-proxy hardening + smoke tests (P0) → 6
- [x] **25** Webhooks HMAC-SHA256 + retry + DLQ + `/time` endpoint → 12
- [x] **26** Cost guardrails enforcement → 12, 25
- [x] **27** Observabilidad: Langfuse self-hosted opcional + OTel → 25
- [x] **28** Community maps trust model: linter 12 reglas + cosign keyless → 16
- [x] **31** Wire FastAPI endpoints to use cases (replace 501 stubs) → 3, 4, 12
- [x] **33** docker-compose full stack (production-equivalent) → 6, 24
- [x] **34** CLI `open-banca register-credentials` interactivo → 5
- [~] **29** **USER-TEST checkpoint 5**: Operations + security → 23, 24, 25, 26, 27, 28

## Fase 6 — Release v1.0.0 (tasks 30, 35)

Objetivo: validación end-to-end con betatesters externos + stress + docs + tag.

- [ ] **30** v1.0.0 release: E2E smoke 3 betatesters + stress 100 scrapes + docs + tag → 29
- [~] **35** **USER-TEST checkpoint 6**: v1.0.0 release validation → 30

## Trabajo en vuelo (no en Taskmaster, plan ad-hoc 2026-05-16)

Plan activo: [`launch-api-bg-scrape`](../.cursor/plans/launch-api-bg-scrape_695d474e.plan.md) — cerrar gaps técnicos detectados antes del primer scrape real BG en producción local:

- [~] **F0** LLM router unificado (single-provider fallback, cost caps dinámicos via `litellm.cost_per_token`).
- [~] **F1** Cerrar workflow: `spawn_sandbox` / `cleanup_sandbox` / `persist_result` / `list_accounts` activities + registrar `human_input_await` en worker.
- [x] **F2** Suite verde: arreglar 24 tests rojos detectados (orchestrator + banco_general).
- [ ] **F3** Mapper en vivo a Banco General + capturar HAR fixture (requiere creds operador).
- [ ] **F4** Deploy local docker-compose con `.env` real.
- [ ] **F5** Smoke E2E real: `POST /scrape` → confirmar OTP en device físico → validar webhook firmado + resultado canónico + cost < $0.50.

## v2+ (out of scope v1, tracking informal)

- Segundo banco (probablemente Banistmo o BAC). Validar que la arquitectura escala sin tocar core.
- Préstamos hipotecarios / personales.
- Inversiones, fideicomisos.
- Auto-apply remap con calibración empírica del Judge ([ADR-0013](../docs/adr/0013-confidence-threshold-remap.md) revisión).
- Captcha resolving (cuando aparezca el primer banco PA que lo use).
- Multi-tenant: NO en core. Quien quiera, lo construye encima.

## Comandos útiles

```bash
task-master next                          # siguiente task disponible
task-master show <id>                     # detalle de un task
task-master list                          # todos los tasks
task-master set-status --id=<id> --status=done   # marcar done después de validation
```

**Regla**: NUNCA marcar `done` un USER-TEST checkpoint sin validación humana explícita.
