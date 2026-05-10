# Overview

## Problema

Bancos de Panamá no exponen Open Banking. La única forma de acceder a la información financiera propia es entrar a la web del banco, navegar manualmente y descargar reportes (típicamente Excel). Tedioso, propenso a errores, no automatizable de forma confiable porque las webs cambian.

## Solución

API REST self-hosted que abstrae la captura de datos detrás de un contrato simple. Internamente:

1. Un **agente Mapper** explora la web del banco y produce un **JSON declarativo** (`map.json`) que describe el flujo: login → navegación → descarga de Excel → parsing.
2. Un **runner determinístico** (Playwright puro, 0 LLM) ejecuta ese mapa en cada scrape.
3. Cuando el runner detecta que el flujo cambió (selector roto, schema mismatch), un **Judge** decide si re-mapear, y un **Remapper** produce un nuevo `map.json`.
4. Un **Validator** verifica sanity de los datos extraídos.
5. La API entrega transacciones/balances normalizados a un schema canónico.

El cliente final hace `POST /scrape` con sus credenciales (encriptadas at-rest), recibe `job_id`, escucha webhooks (`job.otp_required`, `job.completed`, etc.) y consume el resultado.

## Goals (v1)

- API REST self-hostable (1 docker-compose, multi-instancia para Temporal).
- Banco piloto: **Banco General (PA)**.
- Tipos de cuenta: ahorro, corriente, tarjetas de crédito.
- Descarga de Excel oficial → parser declarativo → schema canónico.
- Soporte 2FA con Clave Móvil vía endpoint manual + webhook.
- Self-healing: detección + re-mapeo asistido por LLM con guard-rails.
- Sandbox por banco con network whitelist.
- Secrets at-rest cifrados (AES-GCM + Argon2id, sqlcipher).
- Observabilidad: Langfuse self-hosted opcional + OTel.
- Cost guardrails: presupuesto LLM por job, circuit breakers, rate limits.

## Non-goals (v1)

- Préstamos hipotecarios / personales (v2).
- Inversiones, fideicomisos (v2).
- Captcha resolving — bancos PA no usan captcha por ahora; cuando aparezca, vision LLM o servicio externo (decisión postergada).
- UI / frontend — sólo API.
- Multi-tenant SaaS — el contrato es self-hosted single-org. Si querés multi-tenant, lo construís encima.
- Stealth / anti-bot evasion sofisticado — usamos un real browser via CDP; si el banco bloquea, escalamos.

## Stack

| Área | Tecnología | Razón |
|------|-----------|-------|
| Lenguaje | Python 3.12+ | Stack ya conocido + ecosistema scraping |
| Package manager | uv | Velocidad + reproducibilidad |
| Lint | Ruff | Estándar |
| Type checker | Pyrefly | Por preferencia explícita |
| Web framework | FastAPI | Async + Pydantic nativo |
| Agents | PydanticAI | Encaja con Pydantic + structured output |
| LLM gateway | LiteLLM | Un solo cliente para múltiples proveedores |
| Browser agent | browser-use (lib) | Mapper/Remapper encima, no reinventamos CDP |
| Scraper runner | Playwright puro | Determinístico, 0 LLM, replay-able |
| Excel | openpyxl / pandas | Parser DSL ejecuta sobre estos |
| Workflow engine | Temporal | Durable execution, OTP pause/resume nativo |
| Storage | SQLite + sqlcipher | Single-binary self-host, cifrado at-rest |
| Maps storage | Filesystem + git | Versionable, diffable, signable |
| Observability | Langfuse (LLM) + OTel (resto) | Trazabilidad cross-agent |
| Sandbox | Docker per-job + network policies | Aislamiento por banco |
| Monorepo | Nx + `@nxlv/python` | Aunque Python-first, ya elegido |
| License | AGPL-3.0 | "Presión" intencional sobre forks |

## Modelos LLM

| Agente | Modelo | Vía |
|--------|--------|-----|
| Mapper | Claude Sonnet 4.6 (vision) | LiteLLM → Anthropic |
| Remapper | Claude Sonnet 4.6 (vision) | LiteLLM → Anthropic |
| Validator | DeepSeek V3 (texto) | LiteLLM → DeepSeek |
| Judge | DeepSeek V3 (texto) | LiteLLM → DeepSeek |
| Scraper | **Sin LLM** | Playwright puro |

## Decisiones clave

Ver registro completo en [`adr/`](./adr/). Resumen:

| ID | Decisión | Estado |
|----|----------|--------|
| ADR-0001 | Opción A: mapper-runner separados (no LLM-in-loop) | Aceptado |
| ADR-0002 | Excel-first: bajar reporte oficial, no DOM scrape | Aceptado |
| ADR-0003 | Temporal para orquestación durable | Aceptado |
| ADR-0004 | Multi-agent: Mapper, Scraper, Validator, Judge, Remapper | Aceptado |
| ADR-0005 | PydanticAI + LiteLLM como capa de agentes | Aceptado |
| ADR-0006 | Vision split: Claude para vision, DeepSeek para texto | Aceptado |
| ADR-0007 | Excel parser declarativo (DSL whitelisted) | Aceptado |
| ADR-0008 | sqlcipher + Argon2id + AES-GCM para secrets at rest | Aceptado |
| ADR-0009 | Docker container efímero por scrape job | Aceptado |
| ADR-0010 | License AGPL-3.0 | Aceptado |
| ADR-0011 | Webhook events firmados HMAC | Aceptado |
| ADR-0012 | Schema unificado con discriminator `account_type` | Aceptado |
| ADR-0013 | Auto-remap si confidence ≥ 0.85 AND risk == low, sino HITL | Aceptado |
| ADR-0014 | browser-use como base de Mapper/Remapper | Aceptado |
| ADR-0015 | No browser session persistence cross-job (5-min bank timeout) | Aceptado |
