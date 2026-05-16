# Flow: Remap Detection

Cómo el sistema detecta que el flujo del banco cambió y decide qué hacer. Triggered por cualquier `BreakageEvent` emitido por el Scraper Runner. Resuelve a una decisión Judge: `retry / partial_remap / full_remap / abort / human_required`.

> **v1 — HITL obligatorio para todos los remaps.** Cuando Judge decide `partial_remap` o `full_remap`, el flow siempre continúa por HITL (webhook `job.remap_proposed` + pausa esperando approval del operador). La rama auto-apply está desactivada. `confidence` y `risk` se registran sólo como telemetría para calibración. Ver [ADR-0013 Amendment](../adr/0013-amendment-hitl-only-v1.md).

## Disparadores de detección

```mermaid
flowchart TD
    Run[Scraper Runner ejecuta step] --> Outcome{Outcome del step}

    Outcome -->|ok| Continue[Avanza al siguiente step]

    Outcome -->|selector returns null| C1[selector_missing]
    Outcome -->|step excede timeout| C2[step_timeout]
    Outcome -->|HTTP 4xx/5xx en step conocido| C3[http_error]
    Outcome -->|parser DSL falla a normalizar| C4[schema_drift]
    Outcome -->|texto canary cambió| C5[assertion_failed]
    Outcome -->|mime/extension del download cambió| C6[mime_mismatch]
    Outcome -->|DOM hash relevante cambió vs baseline| C7[dom_drift]

    C1 --> Pack[Empaqueta BreakageEvent]
    C2 --> Pack
    C3 --> Pack
    C4 --> Pack
    C5 --> Pack
    C6 --> Pack
    C7 --> Pack

    Pack --> Capture[Captura: screenshot, dom_hash, last 3 steps, network HAR ref]
    Capture --> J[JudgeActivity]

    J --> Decision{Decisión del Judge}
    Decision -->|retry| Retry[Workflow re-ejecuta el step con backoff]
    Decision -->|partial_remap (web)| PR[Lanza RemapBankWorkflow scope=patch\nRemapper (Vision)]
    Decision -->|partial_remap (data)| PRD[Lanza Parser Generator Agent\nSelf-healing de parser.json]
    Decision -->|full_remap| FR[Lanza RemapBankWorkflow scope=full\nRemapper (Vision)]
    Decision -->|abort| Ab[Workflow → failed]
    Decision -->|human_required| HR[Webhook job.human_required + pausa]
```

## Tipos de causa: detalle

| Causa | Síntoma observable | Ejemplo Banco General |
|-------|-------------------|------------------------|
| `selector_missing` | `page.locator(s).count() == 0` tras `wait_for_selector` con state visible | Botón "Descargar Excel" cambió de class |
| `step_timeout` | Step excede su `timeout_ms` configurado | Modal nuevo bloquea click sin selector conocido |
| `http_error` | Navegación o XHR responde 4xx/5xx en URL conocida del flujo | Endpoint de descarga cambió de path |
| `schema_drift` | Parser DSL falla (`ParserError`, `SchemaValidationError`) | Excel agrega/quita columna, cambia formato fecha, cambia fila de inicio |
| `assertion_failed` | `assert_text` no matchea (literal/regex) | Copy de header cambió |
| `mime_mismatch` | Download recibido con MIME inesperado | Banco devuelve PDF en vez de XLSX |
| `dom_drift` | Hash del frame relevante difiere del baseline > umbral | Rediseño parcial sin romper selectores específicos |

`dom_drift` es **señal débil**: por sí solo no detiene el job (puede ser un banner promocional). Se usa como input adicional para Judge, no como trigger directo de remap.

## Información que recibe Judge

Para cada `BreakageEvent`, Judge (DeepSeek V3 texto) recibe:

- `cause`, `step_id`, `step_type`, `step_index`, `bank_id`, `map_version`.
- `screenshot` — referencia al artefacto, no el binario (Judge es texto; vision queda para Remapper).
- `dom_snapshot_summary` — extracto textual del frame (texto visible, headings, errores).
- `selector_attempted` y `expected` vs `observed`.
- `last_steps` — últimos 3 `StepResult` para contexto.
- `breakage_history_24h` — cuántas veces este banco rompió en las últimas 24 h y cómo.
- `budget_state` — `cost_used_usd`, `remaps_used_24h`, `consecutive_login_failures`.

Judge devuelve estructura: `decision`, `confidence: 0..1`, `risk: low|medium|high`, `rationale` (texto corto), `suggested_scope` (para remaps: `patch` o `full`).

## Mapping causa → decisión sugerida

Tabla heurística usada por Judge como prior. La decisión final puede divergir si el contexto lo justifica.

| Causa | Frecuencia 24h | Sugerencia base | confidence típica | risk típica |
|-------|----------------|-----------------|--------------------|-------------|
| `selector_missing` | 1ª vez | `partial_remap` (web, scope=step) | 0.80 – 0.90 | low |
| `selector_missing` | ≥ 2 distintos en mismo run | `full_remap` | 0.70 – 0.85 | medium |
| `step_timeout` | 1ª vez | `retry` (1 vez con backoff) | 0.70 | low |
| `step_timeout` | recurrente tras retry | `partial_remap` (web) | 0.65 – 0.80 | medium |
| `http_error` 4xx | path conocido | `partial_remap` (web) | 0.75 | low |
| `http_error` 5xx | cualquier path | `retry` (banco caído transitorio) | 0.85 | low |
| `schema_drift` | columna nueva tolerable, cambio de fila | `partial_remap` (data, scope=parser) | 0.80 | low |
| `schema_drift` | columna obligatoria desaparece | `partial_remap` (data, scope=parser) | 0.60 | high |
| `assertion_failed` | copy menor | `retry` con `assert` relajado a regex | 0.70 | low |
| `mime_mismatch` | XLSX → PDF | `human_required` (cambio de canal) | 0.50 | high |
| `dom_drift` solo | sin otro síntoma | ignorar (no escalar) | N/A | N/A |
| Cualquiera + `consecutive_login_failures ≥ 2` | | `abort` (circuit breaker) | 0.95 | high |
| Cualquiera + `remaps_used_24h ≥ 3` | | `human_required` (cap excedido) | 0.95 | high |

## Decisiones del Judge

| Decisión | Acción del workflow | Costo | Cuándo |
|----------|---------------------|-------|--------|
| `retry` | Re-ejecuta el mismo step con backoff exponencial (max 2 intentos extra) | $0 | causa transitoria, recoverable sin cambios |
| `partial_remap` (web) | Lanza `RemapBankWorkflow` con `scope=step`; sólo se patchea el `step` afectado | Claude vision, $0.05 – $0.15 | cambio localizado en la web |
| `partial_remap` (data) | Lanza `Parser Generator Agent` para auto-reparar el `parser.json` usando el Excel fallido | Claude/DeepSeek texto, $0.01 – $0.05 | cambio en la estructura del Excel (`schema_drift`) |
| `full_remap` | Lanza `RemapBankWorkflow` con `scope=full`; Remapper rehace `map.json` desde cero | Claude vision, $0.20 – $0.40 | cambio sistémico, múltiples breakages |
| `abort` | Workflow → `failed`, webhook `job.failed` con causa | $0 | circuit breaker abierto, budget excedido, error fatal no remap-able |
| `human_required` | Pausa workflow, webhook `job.human_required` con evidencia, espera intervención manual | $0 (LLM); humano sí | cambio de canal (PDF en vez de XLSX), captcha aparece, fraude flag |

## Guardrails que sobreescriben al Judge

- **Cost cap**: si `cost_used_usd + estimated_remap_cost > $0.50` → forzar `human_required`.
- **Remap cap**: si `remaps_used_24h ≥ 3` para el banco → forzar `human_required`.
- **Login circuit**: si `consecutive_login_failures ≥ 2` → `abort` con `circuit_open_1h`.
- **Confidence floor**: si Judge devuelve `confidence < 0.50` con sugerencia de remap → degradar a `human_required` (no autorizar remap inseguro).

## Outputs

- `BreakageEvent` persistido en storage para análisis.
- `JudgeDecision` persistida (`decision`, `confidence`, `risk`, `rationale`) — `confidence` y `risk` son telemetría en v1, no gates de auto-apply.
- Si `partial_remap`/`full_remap` → siempre input para [`03-flows/remap-approval.md`](./remap-approval.md) vía HITL (v1).
- Métricas: `breakage_total` por causa, `judge_decision_total` por tipo, `remaps_blocked_by_cap_total`.

## Referencias

- Aprobación de remap: [`03-flows/remap-approval.md`](./remap-approval.md)
- Recovery patterns: [`03-flows/error-recovery.md`](./error-recovery.md)
- ADR auto-remap threshold: `adr/0013-confidence-threshold-remap.md` (existente)
- ADR-0013 Amendment v1 HITL-only: [`../adr/0013-amendment-hitl-only-v1.md`](../adr/0013-amendment-hitl-only-v1.md)
- Scraper Runner: [`02-components/scraper-runner.md`](../02-components/scraper-runner.md)
