# Documentación — open-banca

Índice navegable de los 46 documentos de diseño de `open-banca`. Punto de entrada canónico para entender el sistema antes de tocar código.

## Lectura por orden recomendado

1. [00-overview.md](./00-overview.md) — visión, scope, no-goals, stack, modelos LLM.
2. [DECISIONS.md](./DECISIONS.md) — registro de decisiones de la sesión inicial con cross-refs a ADRs.
3. [01-architecture/macro.md](./01-architecture/macro.md) — vista C4 nivel 2 del sistema.
4. [01-architecture/multi-agent.md](./01-architecture/multi-agent.md) — orquestación de los 5 agentes.
5. [03-flows/full-historical-scrape.md](./03-flows/full-historical-scrape.md) — flujo end-to-end del primer scrape.
6. [04-security/threat-model.md](./04-security/threat-model.md) — STRIDE + trust boundaries.

## Por categoría

### 01 — Arquitectura

| Doc | Qué cubre |
|-----|-----------|
| [macro.md](./01-architecture/macro.md) | Diagrama de componentes C4 nivel 2, protocolos, failure domains |
| [hexagonal.md](./01-architecture/hexagonal.md) | Capas dominio/aplicación/adaptadores, 11 ports nombrados |
| [multi-agent.md](./01-architecture/multi-agent.md) | 5 agentes (Mapper, Scraper, Validator, Judge, Remapper), routing confidence/risk |
| [data-flow.md](./01-architecture/data-flow.md) | Sequence diagrams happy path + flujo de ruptura |

### 02 — Componentes

| Doc | Qué cubre |
|-----|-----------|
| [api.md](./02-components/api.md) | Contrato HTTP REST de FastAPI, 10 endpoints, idempotency, auth |
| [orchestrator.md](./02-components/orchestrator.md) | `ScrapeJobWorkflow`, child workflows, 11 activities, 3 signals |
| [scraper-runner.md](./02-components/scraper-runner.md) | Playwright runner sin LLM, step types, `BreakageEvent` |
| [mapper-agent.md](./02-components/mapper-agent.md) | Agente Mapper sobre browser-use + Claude vision |
| [validator-agent.md](./02-components/validator-agent.md) | Validator (DeepSeek texto) + checks heurísticos |
| [judge-agent.md](./02-components/judge-agent.md) | Judge: decisiones retry/remap/abort + confidence/risk |
| [parser.md](./02-components/parser.md) | DSL declarativo Excel, helpers whitelist, account_type inference |
| [storage.md](./02-components/storage.md) | SQLite/sqlcipher + filesystem maps, dedup 3 niveles |
| [secrets.md](./02-components/secrets.md) | Vault: store/retrieve/inject/rotate + audit log |
| [webhooks.md](./02-components/webhooks.md) | 7 eventos, HMAC-SHA256, retry exponential, DLQ |

### 03 — Flujos end-to-end

| Doc | Qué cubre |
|-----|-----------|
| [full-historical-scrape.md](./03-flows/full-historical-scrape.md) | Primer scrape de un banco, mapping + login + descarga + parse |
| [incremental-scrape.md](./03-flows/incremental-scrape.md) | Scrape periódico con `since` cursor + dedup merge |
| [otp-pause-resume.md](./03-flows/otp-pause-resume.md) | Pause con Clave Móvil, signal Temporal, hard cap 4 min |
| [remap-detection.md](./03-flows/remap-detection.md) | 7 causas de ruptura, mapping causa→decisión Judge |
| [remap-approval.md](./03-flows/remap-approval.md) | Auto-apply (confidence ≥ 0.85 + risk low) vs HITL |
| [error-recovery.md](./03-flows/error-recovery.md) | 16 categorías de error con detección/recovery/escalation |

### 04 — Seguridad

| Doc | Qué cubre |
|-----|-----------|
| [threat-model.md](./04-security/threat-model.md) | STRIDE: 17 amenazas sobre 8 activos, severidad, controles |
| [sandbox.md](./04-security/sandbox.md) | Docker per-job, network policy, inyección de creds tmpfs |
| [community-maps.md](./04-security/community-maps.md) | Linter 12 reglas, cosign signing, pipeline CI |
| [secrets-at-rest.md](./04-security/secrets-at-rest.md) | Argon2id + AES-GCM + sqlcipher, rotación, backup, fugas |

### 05 — Operaciones

| Doc | Qué cubre |
|-----|-----------|
| [deployment.md](./05-operations/deployment.md) | docker-compose layout, primer arranque, escalado |
| [observability.md](./05-operations/observability.md) | Langfuse + OTel, trace correlation, SLI/SLO |
| [cost-guardrails.md](./05-operations/cost-guardrails.md) | Caps LLM, rate limits, circuit breakers, alertas |
| [testing.md](./05-operations/testing.md) | HAR replay, Excel fixtures, LLM mocks, smoke real |

### 06 — Bancos

| Doc | Qué cubre |
|-----|-----------|
| [banco-general.md](./06-banks/banco-general.md) | Banco piloto: Clave Móvil, Excel formats, quirks, open questions |

### ADRs (Architecture Decision Records)

Formato Michael Nygard: Context / Decision / Consequences / Alternatives. Todos `Status: Accepted (2026-05-09)`.

| ID | Decisión |
|----|----------|
| [0001](./adr/0001-opcion-a-mapper-runner-split.md) | Mapper-Runner split (no LLM-in-loop) |
| [0002](./adr/0002-excel-download-strategy.md) | Excel-first (descargar reporte, no DOM scrape) |
| [0003](./adr/0003-temporal-orchestration.md) | Temporal para durable workflows |
| [0004](./adr/0004-multi-agent-architecture.md) | 5 agentes con roles distintos |
| [0005](./adr/0005-pydanticai-litellm.md) | PydanticAI + LiteLLM como capa de agentes |
| [0006](./adr/0006-vision-split-claude-deepseek.md) | Vision split: Claude vision / DeepSeek texto |
| [0007](./adr/0007-declarative-excel-dsl.md) | Excel parser DSL declarativo whitelisted |
| [0008](./adr/0008-sqlcipher-secrets.md) | sqlcipher + Argon2id + AES-GCM |
| [0009](./adr/0009-docker-sandbox-per-job.md) | Docker container efímero por scrape job |
| [0010](./adr/0010-agpl-license.md) | License AGPL-3.0 |
| [0011](./adr/0011-webhook-events-hmac.md) | Webhooks firmados HMAC-SHA256 |
| [0012](./adr/0012-unified-account-schema.md) | Schema unificado discriminator `account_type` |
| [0013](./adr/0013-confidence-threshold-remap.md) | Auto-remap si confidence ≥ 0.85 + risk low, sino HITL |
| [0014](./adr/0014-browser-use-as-mapper-foundation.md) | browser-use lib como base Mapper/Remapper |
| [0015](./adr/0015-no-session-persistence-v1.md) | No browser session persistence cross-job |

## Convenciones

- Diagramas en **Mermaid** embebidos. Comentarios en español.
- Texto en **español**. Tecnicismos canónicos en inglés (workflow, activity, hexagonal, port, HMAC, KDF, etc.).
- Sin clases ni código Python concreto en docs — sólo nombres conceptuales y relaciones.
- ADRs cierran con `Status: Accepted (YYYY-MM-DD)`.

## Próximos pasos (post-synthesis)

1. PRD canónico vía `prd-taskmaster` skill para handoff a TaskMaster.
2. Issues GitHub para los **5 riesgos críticos pre-implementación** del threat model:
   - Filter middleware redact + canary obligatorio en CI.
   - `docker-socket-proxy` golden config + smoke test endpoints prohibidos.
   - browser-use `sensitive_data` fuzz test (placeholder en prompt vs valor en tool call).
   - Cosign keyless GitHub OIDC vs HSM para firma maps oficiales.
   - Webhook clock skew ±5 min + endpoint `/time` para diagnosis cliente.
3. Primer mapping real contra Banco General — validar quirks documentados.
