# Tech Stack

> Decisiones técnicas core. Cada una respaldada por un ADR (referenciado). Síntesis ejecutiva — el racional completo vive en `docs/adr/`.

## Lenguaje y tooling

| Área | Elección | Por qué |
|------|----------|---------|
| Lenguaje | **Python 3.12+** | Ecosistema scraping maduro (Playwright, openpyxl, pandas). Type hints modernos. |
| Package manager | **uv** (workspaces) | Velocidad (10-100x pip), lockfile reproducible, soporte monorepo nativo. |
| Lint + format | **Ruff** | Estándar de facto. Reemplaza black + isort + flake8 + más. |
| Type checker | **Pyrefly** | Preferencia explícita del operador. |
| Monorepo | **Nx + `@nxlv/python`** | Tasks orchestration, affected detection, cache. Aunque Python-first, ya elegido. |

## Arquitectura

**Hexagonal (ports & adapters)** — separación estricta domain ↔ adapters:

```
domain/        — entities, value objects, ports (puro, sin deps externas)
application/   — use cases (orquestan domain + ports)
adapters/
  api/         — FastAPI
  orchestrator/ — Temporal workflows + activities
  browser/     — browser-use (Mapper/Remapper) + Playwright (Runner)
  llm/         — PydanticAI + LiteLLM (router single-provider)
  storage/     — SQLite + sqlcipher repositories
  parsing/     — Excel DSL engine
  sandbox/     — Docker per-job wrapper
banks/<id>/    — map.json + parser.json + fixtures HAR + tests por banco
shared/schemas/ — Pydantic models cross-package
```

## API + orquestación

| Componente | Tecnología | ADR | Razón |
|-----------|-----------|-----|-------|
| Web framework | **FastAPI + Pydantic v2** | — | Async nativo, OpenAPI auto, integración Pydantic perfecta. |
| Workflow engine | **Temporal (Python SDK)** | [ADR-0003](../docs/adr/0003-temporal-orchestration.md) | Durable execution. OTP pause/resume vía signals nativo. Time-skipping tests. Replay determinístico. |
| Sidecar browser | **BrowserSidecar process aparte** | [ADR-0019](../docs/adr/0019-browser-sidecar-process.md) | Sobrevive a restart del worker Temporal durante OTP wait (≤4 min). Resuelve P0-1. |
| Auth API | Bearer API key | — | Single-org self-hosted; sin OAuth en v1. |
| Rate limit | slowapi | — | 60 req/min global; límites adicionales en `/scrape`. |

## LLM stack

| Componente | Tecnología | ADR | Razón |
|-----------|-----------|-----|-------|
| Agent framework | **PydanticAI** | [ADR-0005](../docs/adr/0005-pydanticai-litellm.md) | Structured output con Pydantic, encaja con stack. Test models nativos. |
| LLM gateway | **LiteLLM** | [ADR-0005](../docs/adr/0005-pydanticai-litellm.md) | Un cliente para Anthropic / DeepSeek / OpenAI / etc. Pricing table built-in. |
| LLM router | **`open_banca_llm.router`** (interno) | — | Resolver single-provider: lee env, cae al provider disponible. Si solo hay `ANTHROPIC_API_KEY`, los 4 agentes usan Claude. |
| Mapper / Remapper | **Claude Sonnet 4.6 + vision** (default) | [ADR-0006](../docs/adr/0006-vision-split-claude-deepseek.md) | Vision capability necesaria. Calidad alta para tarea crítica. |
| Validator / Judge | **DeepSeek V3** (default) o **Claude Haiku** (fallback) | [ADR-0006](../docs/adr/0006-vision-split-claude-deepseek.md) | Texto only. DeepSeek es ~30x más barato. |
| Browser agent base | **browser-use** (path dep local) | [ADR-0014](../docs/adr/0014-browser-use-as-mapper-foundation.md) | CDP + agent loop ya resueltos. No reinventamos. |

## Scraping + parsing

| Componente | Tecnología | ADR | Razón |
|-----------|-----------|-----|-------|
| Browser automation | **Playwright (Chromium)** | — | Mejor stack para web moderna. CDP via browser-use para Mapper. |
| Scraper Runner | **Playwright puro, 0 LLM** | [ADR-0001](../docs/adr/0001-opcion-a-mapper-runner-split.md) | Determinístico, replay-able, sin costo LLM por corrida. 9 step types declarativos. |
| Excel parser | **openpyxl** + DSL whitelisted | [ADR-0007](../docs/adr/0007-declarative-excel-dsl.md) + [amendment](../docs/adr/0007-declarative-excel-dsl.md#amendment) | AST whitelist (no eval/exec/import). `re2` (no ReDoS), `defusedxml` (no zip-bomb), budgets memoria/tiempo. |
| Map storage | **Filesystem + git** | — | Versionable, diffable, cosign-signable. Maps community en `community/` con warning. |
| Map signing | **cosign keyless (GitHub OIDC)** | — | Maps oficiales firmados; runtime rechaza no firmados. |

## Storage + secrets

| Componente | Tecnología | ADR | Razón |
|-----------|-----------|-----|-------|
| App DB | **SQLite + sqlcipher** | [ADR-0008](../docs/adr/0008-sqlcipher-secrets.md) | Single-binary self-host. Cifrada at-rest. |
| Secret vault | **Argon2id KDF → AES-GCM** | [ADR-0008](../docs/adr/0008-sqlcipher-secrets.md) + [amendment](../docs/adr/0008-sqlcipher-secrets.md#amendment) | Master passphrase via env (mlock + no swap + no core dumps). **NUNCA** en config files. |
| Webhook outbox | SQLite separada | — | DLQ + retry exponencial independiente del job storage. |
| Temporal state | Postgres (en docker-compose) | — | Requisito del server Temporal. |

## Seguridad

| Capa | Mecanismo | ADR |
|------|-----------|-----|
| Credenciales bancarias | Cifradas at-rest, NUNCA plaintext en logs/Langfuse/OTel/HAR. Canary CI test obligatorio en cada PR. | [ADR-0008](../docs/adr/0008-sqlcipher-secrets.md) |
| Sensitive data → LLM | browser-use `sensitive_data` API: el LLM nunca recibe plaintext. Fuzz test instrumenta LiteLLM. | [ADR-0020](../docs/adr/0020-pii-redact-llm-boundary.md) |
| PII en screenshots/DOM → LLM | Middleware redact antes de provider (Ley 81 PA Art. 13). | [ADR-0020](../docs/adr/0020-pii-redact-llm-boundary.md) |
| Sandbox por job | Docker container efímero. Network allowlist (banco + APIs LLM). Caps drop, read-only rootfs, non-root. | [ADR-0009](../docs/adr/0009-docker-sandbox-per-job.md) |
| Docker socket | **docker-socket-proxy** entre worker y daemon. | [ADR-0009 amendment] |
| Excel parser | DSL whitelisted, AST rechaza `eval/exec/import`. | [ADR-0007 amendment](../docs/adr/0007-declarative-excel-dsl.md#amendment) |
| Maps community | cosign keyless firma. Runtime rechaza maps no firmados. | — |
| License | AGPL-3.0 (forks/servicios derivados deben publicar código). | [ADR-0010](../docs/adr/0010-license-agpl3.md) |

## Cost guardrails

Enforced centralmente per [ADR-0026](../docs/adr/) y `docs/05-operations/cost-guardrails.md`:

- **Max $0.50 LLM cost / scrape job** (suma de todos los agentes).
- **Cap dinámico por agente y modelo**: Validator $0.05-$0.30, Judge $0.02-$0.20 (según provider activo).
- Max **3 remap attempts / banco / 24h**.
- Max **2 logins fallidos consecutivos** → circuit breaker 1h por banco/cuenta.
- Max **1 mapping run / banco / 24h** (defensivo, override por config).

## Observabilidad

| Layer | Tool | Cuándo |
|-------|------|--------|
| LLM traces | **Langfuse self-hosted** (opcional, profile-gated) | Debug agentes IA. Cloud rompe principio self-host. |
| App traces/metrics/logs | **OpenTelemetry** | Profile-gated. Exporters configurables. |
| Browser session | Playwright traces (HAR + video) | Capturado por job para debug. |
| Browser visibility | Visible en debug mode (env), headless en producción. | — |

## Webhooks

| Aspecto | Decisión | ADR |
|---------|----------|-----|
| Firma | **HMAC-SHA256** con secret en config | [ADR-0011](../docs/adr/0011-webhook-events-hmac.md) |
| Retry | Exponencial (5s, 10s, 20s, ...) hasta 5 attempts | — |
| DLQ | SQLite outbox separada | — |
| Endpoint de test | `POST /webhooks/test` con `/time` echo | — |

## Testing

| Tipo | Tool | Notas |
|------|------|-------|
| Unit + integration | **pytest + pytest-asyncio** | Coverage gate en CI. |
| Workflow tests | **Temporal time-skipping** (`temporalio.testing`) | OTP / retry sin reloj real. |
| Browser tests | **Playwright HAR record/replay** | Fixtures por banco bajo `banks/<id>/fixtures/har/`. |
| LLM tests | **PydanticAI test models** | Zero gasto de tokens en CI. |
| Property-based | **hypothesis** | Parser DSL, dedup engine. |
| Smoke vs banco real | **Opt-in via env** (`OPEN_BANCA_LIVE_SMOKE=1`, `OPEN_BANCA_LIVE_MAPPER=1`) | Creds del operador. NO corre en CI. Marker `@pytest.mark.live`. |

## Browser session

**Sin persistencia cross-job** ([ADR-0015](../docs/adr/0015-no-session-persistence-v1.md)). Razón: bancos PA cierran sesión a ~5 min de inactividad; cookies cacheadas son inútiles a la siguiente corrida.

**Sí persistimos contexto dentro del mismo job activo** (durante OTP pause), serializado via Temporal activity heartbeat + BrowserSidecar process.

## Convenciones

- Diagramas en **Mermaid** (embedidos en .md).
- Documentación en **español**, tecnicismos canónicos en **inglés**.
- ADRs en formato Nygard (Context, Decision, Consequences, Alternatives) cerrando con `Status: Accepted (YYYY-MM-DD)`.
- Commits: **Conventional Commits** + fragment del task ID (`feat(task-12): ...`).
- Branch por task: `task-{id}-{slug}`. Merge a main con tag checkpoint en USER-TEST.
- Idioma código y comentarios: inglés (interop futuro). Idioma docs/PRD/ADR: español.
