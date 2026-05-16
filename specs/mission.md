# Mission

> Constitution document — north star del proyecto. Síntesis ejecutiva. Para detalle ver `docs/`, `.taskmaster/docs/prd.txt` y `docs/adr/`.

## Qué construimos

**open-banca**: API REST self-hosted y open source (AGPL-3.0) que automatiza la captura de datos bancarios de Panamá usando agentes de IA + Playwright + Temporal. Banco piloto: **Banco General**.

Contrato simple para el cliente final:
1. `POST /scrape` con `bank_id` + referencia a credencial cifrada.
2. Recibe `job_id`. Escucha webhooks firmados HMAC (`job.otp_required`, `job.completed`, etc.).
3. `GET /jobs/{id}/result` devuelve transacciones y balances en un schema canónico unificado.

Internamente, 5 agentes desacoplados:

| Rol | Tecnología | Cuándo corre |
|-----|------------|--------------|
| **Mapper** | browser-use + Claude vision | Primera vez por banco. Genera `map.json` declarativo. |
| **Scraper Runner** | Playwright puro, **0 LLM** | Cada corrida. Ejecuta el `map.json`. Determinístico. |
| **Validator** | DeepSeek V3 (o Claude Haiku fallback) | Post-scrape. Heurísticas + LLM solo si ambiguo. |
| **Judge** | DeepSeek V3 (o Claude Haiku fallback) | Cuando el runner detecta breakage. Decide ruta. |
| **Remapper** | browser-use + Claude vision | Cuando Judge decide re-mapear. Patch o full map. |

## Por qué

### Problema
Bancos de Panamá no exponen Open Banking. La única forma de acceder a la información financiera propia es entrar a la web, navegar manualmente y descargar Excel. Tedioso, error-prone, no automatizable de forma confiable porque las webs cambian.

### Solución técnica diferenciada
**Mapper-Runner split** ([ADR-0001](../docs/adr/0001-opcion-a-mapper-runner-split.md)): el LLM produce un mapa declarativo una vez; el runner lo ejecuta sin LLM cada corrida. Reduce costo (~$0.50/job max vs $5+ con LLM-in-loop) y blast radius (replay determinístico, sin no-determinismo cross-run). Cuando la web cambia, el Judge decide si vale la pena gastar LLM en remapear.

**Excel-first** ([ADR-0002](../docs/adr/0002-excel-download-strategy.md)): bajar el reporte oficial del banco en lugar de DOM-scrapear transacciones. Más estable (los bancos cambian más la UI que el formato Excel), más rápido, parseable con DSL whitelisted (no Python arbitrario).

**HITL self-healing** ([ADR-0013](../docs/adr/0013-confidence-threshold-remap.md) + amendment): cuando algo se rompe, Judge propone, operador aprueba vía endpoint, Remapper aplica. v1 es siempre HITL para reducir riesgo; v2 abrirá auto-apply con calibración empírica.

### Por qué AGPL-3.0
Goal secundario: **presión institucional sobre bancos PA**. La licencia AGPL fuerza que cualquier servicio derivado quede abierto. Si un banco o intermediario ofrece esto como producto cerrado, viola la licencia. Si lo usan internamente, está OK. Esto convierte a open-banca en un equalizer: la única forma legal de competir es ofrecer Open Banking de verdad.

## Audiencia

- **Operadores self-hosted**: devs / pymes / individuos que quieren su data sin pasar por intermediarios cerrados.
- **Contribuidores**: gente que quiere agregar más bancos PA (map.json + parser.json + fixtures).
- **Agentes de IA** (Claude, Codex, etc.) que mantienen y extienden el código: este documento es punto de partida obligatorio.

## Goals v1

- Self-hostable en 1 `docker-compose up` (stack completo: API + Temporal + worker + postgres + sandbox).
- Cuentas: **ahorro, corriente, tarjetas de crédito** para Banco General.
- 2FA Clave Móvil vía webhook + endpoint manual de confirmación.
- Self-healing con guard-rails (cost cap, rate limits, HITL approval).
- Secrets cifrados at-rest (Argon2id + AES-GCM + sqlcipher, mlock + no swap).
- Cost guardrails: max **$0.50 LLM/job**, 3 remap/24h por banco, circuit breakers.
- Observabilidad opcional (Langfuse self-hosted + OTel).

## Non-goals v1

- Préstamos hipotecarios / personales (v2).
- Inversiones, fideicomisos (v2).
- Captcha resolving (postergado hasta que aparezca el primer banco con captcha).
- UI / frontend (sólo API; el cliente construye su UI).
- Multi-tenant SaaS (es single-org self-hosted; multi-tenant se construye encima).
- Stealth / anti-bot evasion sofisticado (usamos browser real via CDP; si banco bloquea, escalamos).

## Métricas de éxito v1

- 1 banco productivo (Banco General) con ≥95% éxito de scrape en una semana de corridas reales.
- 3 betatesters externos completan un scrape end-to-end sin asistencia.
- 100 scrapes consecutivos sin leak de credenciales (canary CI verde).
- Cost real ≤ $0.50/job en p95.
