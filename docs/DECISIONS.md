# Registro de decisiones — sesión inicial

Documento ancla con todas las decisiones tomadas durante el design dialogue inicial. Cada item enlaza a su ADR formal.

## Producto y scope

- **Producto**: API REST self-hostable, open source, AGPL-3.0. Acceso a info financiera de bancos PA. Goal secundario: presión institucional.
- **Banco piloto**: Banco General (el más grande de PA).
- **v1 scope**: ahorro + corriente + tarjetas de crédito. Préstamos a v2.
- **Multi-cuenta**: detect all + opt-out via filtro. Default = todo.
- **Range temporal**: full historical primer scrape, luego incremental con `since` cursor + dedup merge.

## Estrategia de captura

- **Excel-first**: el agente navega y dispara descarga del Excel oficial del banco. NO DOM scrape de transacciones. ([ADR-0002](./adr/0002-excel-download-strategy.md))
- **Mapper genera JSON declarativo** (selectores + flujo + filtros). **Scraper runner ejecuta sin LLM** (Playwright puro, determinístico). ([ADR-0001](./adr/0001-opcion-a-mapper-runner-split.md))
- **Excel parser declarativo** (DSL con helpers whitelist: `parse_date`, `extract_regex`, `normalize_amount`). Sin Python arbitrario, encaja con sandbox. ([ADR-0007](./adr/0007-declarative-excel-dsl.md))

## Multi-agent

- **Roles** ([ADR-0004](./adr/0004-multi-agent-architecture.md)):
  - **Mapper** — Claude Sonnet 4.6 + vision. First-time mapping.
  - **Scraper** — sin LLM, Playwright puro. Cada corrida.
  - **Validator** — DeepSeek V3 texto. Post-scrape sanity.
  - **Judge** — DeepSeek V3 texto. Decide retry / partial-remap / full-remap / abort / escalate.
  - **Remapper** — Claude Sonnet 4.6 + vision. Patch/full re-map.
- **Mapper + Remapper se construyen sobre la librería `browser-use`** (CDP + agent loop ya resueltos). ([ADR-0014](./adr/0014-browser-use-as-mapper-foundation.md))
- **PydanticAI + LiteLLM** para Validator/Judge y como integration layer. ([ADR-0005](./adr/0005-pydanticai-litellm.md))
- **Vision split**: Claude para Mapper/Remapper, DeepSeek para Validator/Judge. ([ADR-0006](./adr/0006-vision-split-claude-deepseek.md))

## Aprobación de remap

- **Híbrido** ([ADR-0013](./adr/0013-confidence-threshold-remap.md)):
  - Judge emite `confidence: 0..1` + `risk: low|medium|high`.
  - Auto-apply si `confidence >= 0.85 AND risk == low`.
  - Sino → `job.remap_proposed` webhook + endpoint de approval humano.

## Orquestación y estado

- **Temporal** para durable workflows. ([ADR-0003](./adr/0003-temporal-orchestration.md))
- Justifica el peso operativo (server + worker) por: OTP pause/resume nativo (signals), retries built-in, time-skipping testing, replay determinístico.
- Self-host implica multi-instancia (API + Temporal server + worker + sandbox runner).

## OTP / 2FA

- **Banco General usa Clave Móvil** (push a app, no SMS).
- **Cliente confirma manual** vía `POST /jobs/{id}/otp-confirmed` después de aceptar push en su app.
- API emite webhook `job.otp_required` cuando llega a esa fase.
- Riesgo crítico: **sesión bancaria expira ~5 min**. Hard cap de espera OTP = 4 min. Si excede, abort + retry.

## Schema canónico

- **Unificado con discriminator `account_type`** ([ADR-0012](./adr/0012-unified-account-schema.md)).
- Tipos: `savings`, `checking`, `credit_card`. (Préstamos en v2.)
- Campos comunes + payload específico por tipo. `credit_card` agrega `credit_limit`, `available_credit`, `cut_date`, `min_payment`, `payment_due_date`, `statement_balance` (extendido durante synthesis pass para reflejar lo modelado en ADR-0012).

## Dedup

- Vive **en la API**, no en el cliente. Razón: cross-account transfer matching requiere ver todas las cuentas juntas.
- Estrategia heredada de Banistmo: 3 niveles (ID embebido si existe → fingerprint hash → fuzzy match para transfers internas).

## Webhooks

- **Eventos** ([ADR-0011](./adr/0011-webhook-events-hmac.md)):
  - `job.created`
  - `job.otp_required`
  - `job.progress` (opcional)
  - `job.completed`
  - `job.failed`
  - `job.remap_proposed` (HITL)
  - `job.human_required` (Judge escaló)
- **Firma HMAC-SHA256** con secret en config.
- Sin SSE en v1. Webhook + polling de `GET /jobs/{id}`.

## Seguridad

- **Secrets at rest** ([ADR-0008](./adr/0008-sqlcipher-secrets.md)):
  - Master passphrase via env `OPEN_BANCA_MASTER_PASSPHRASE` o prompt al boot.
  - Argon2id KDF → AES-GCM → SQLite cifrada con sqlcipher.
  - API keys (Anthropic/DeepSeek/Langfuse) en `.env`, responsabilidad del operador.
- **Sandbox por banco** ([ADR-0009](./adr/0009-docker-sandbox-per-job.md)):
  - Container Docker efímero por scrape job.
  - Network policy: solo dominio del banco + APIs LLM whitelisted. Drop everything else.
  - No persistencia de filesystem cross-job (excepto downloads montados como volumen).
- **Maps community**:
  - DSL declarativo ya elimina ejecución de código arbitrario.
  - Maps oficiales firmados (sigstore/cosign).
  - Linter estático en CI valida selectores, URLs, schema.
  - `community/` folder con warning explícito.

## Cost guardrails

- Max **$0.50 LLM cost por scrape job** (DeepSeek hace esto trivial; cap más alto si Mapper/Remapper corren con Claude).
- Max **3 remap attempts por banco por 24h**.
- Max **2 logins fallidos consecutivos** → circuit breaker 1h por banco/cuenta.
- Max **1 mapping run por banco por 24h** (defensivo, override por config).
- Token budget per agent per job, abort si excede.

## Browser session

- **No persistence cross-job** ([ADR-0015](./adr/0015-no-session-persistence-v1.md)). Razón: bancos PA cierran sesión a ~5 min de inactividad; cookies cacheadas son inútiles a la siguiente corrida.
- **Sí persistimos contexto dentro del mismo job activo** (durante OTP pause), serializado por Temporal en activity heartbeat.

## Observabilidad

- **Langfuse self-hosted opcional** (docker-compose service). Cloud rompe principio self-host.
- **OTel** para traces/metrics/logs no-LLM.
- Playwright traces (HAR + video) capturados por job para debug.
- Browser **visible en debug mode** (env var), **headless en producción**.

## Testing

- **Playwright HAR record/replay** para tests integración sin pegarle al banco.
- Fixtures Excel reales anonimizadas para parser tests.
- LLM mocks vía PydanticAI test models.
- Smoke test contra banco real opt-in (env var, requiere credenciales reales del operador).

## Monorepo

- **Nx + `@nxlv/python`** (decisión usuario). Paquetes:
  - `domain/` — entities, value objects, ports.
  - `application/` — use cases (ExecuteScrape, RemapBank, ResolveOTP, ApproveRemap).
  - `adapters/api/` — FastAPI.
  - `adapters/orchestrator/` — Temporal workflows + activities.
  - `adapters/browser/` — browser-use wrapper (Mapper/Remapper) + Playwright runner (Scraper).
  - `adapters/llm/` — LiteLLM + PydanticAI integration.
  - `adapters/storage/` — SQLite/sqlcipher repositories.
  - `adapters/parsing/` — Excel DSL engine.
  - `adapters/sandbox/` — Docker exec wrapper.
  - `banks/banco_general/` — `map.json`, `parser.json`, fixtures, tests.
  - `shared/schemas/` — Pydantic models cross-package.
