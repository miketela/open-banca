# Review de consistencia: PRD ↔ ADRs ↔ Docs de componentes

**Fecha**: 2026-05-10  
**Revisor**: Claude Sonnet 4.6 (automated)  
**Archivos analizados**: `.taskmaster/docs/prd.txt`, `docs/DECISIONS.md`, `docs/02-components/*.md` (10 archivos), `docs/adr/*.md` (15 ADRs)

---

## 1. PRD REQ-001: "10 endpoints" → api.md

**Estado: CONSISTENTE**

`api.md:28-39` lista exactamente 10 endpoints en la tabla:

| # | Método | Path |
|---|--------|------|
| 1 | POST | `/scrape` |
| 2 | GET | `/jobs/{id}` |
| 3 | GET | `/jobs/{id}/result` |
| 4 | POST | `/jobs/{id}/otp-confirmed` |
| 5 | POST | `/jobs/{id}/cancel` |
| 6 | POST | `/maps/{bank}/proposals/{id}/approve` |
| 7 | POST | `/maps/{bank}/proposals/{id}/reject` |
| 8 | GET | `/banks` |
| 9 | GET | `/accounts` |
| 10 | POST | `/webhooks/test` |

El PRD en REQ-001 lista `approve|reject` como un sólo item abreviado pero `api.md` los abre en dos filas separadas — el conteo final es 10 en ambos lados. **Sin inconsistencia numérica.**

Nota menor: `api.md:64` documenta un endpoint adicional `GET /webhooks/dlq` y `POST /webhooks/dlq/:id/replay` en el cuerpo del texto de `webhooks.md:64` como endpoints admin del DLQ, pero éstos no aparecen en la tabla de `api.md` ni en el PRD. No es una inconsistencia crítica (son endpoints operacionales, no del contrato principal), pero sería bueno aclararlo.

---

## 2. PRD REQ-002: "11 activities + 3 signals" → orchestrator.md

**Estado: INCONSISTENCIA MENOR — el doc muestra 10, el PRD dice 11**

`orchestrator.md:41-57` lista **10 activities** en la tabla de inventario:

1. `LoginActivity`
2. `OTPSignalAwaitActivity`
3. `NavigateActivity`
4. `DownloadExcelActivity`
5. `ParseExcelActivity`
6. `ValidateActivity`
7. `JudgeActivity`
8. `MapperAgentActivity`
9. `RemapperAgentActivity`
10. `EmitWebhookActivity`

El PRD (REQ-002) lista los mismos 10 con `etc.` al final, implicando una undécima no nombrada. El doc sólo tiene 10. **El `etc.` del PRD crea ambigüedad**: o el PRD tiene un conteo erróneo (+1), o hay una actividad sin documentar.

Candidato plausible para la undécima: `StoreParsedDataActivity` o `CommitResultActivity` — ningún activity de escritura a storage aparece en la tabla aunque el workflow debe persistir resultados (el flujo en `orchestrator.md:71-88` muestra `running → completed` con `storage commit` pero no hay un activity nombrado).

**Acción requerida**: o añadir el activity que persiste resultados en `orchestrator.md`, o corregir el PRD de "11" a "10".

Los **3 signals** sí coinciden en ambos lados: `otp_confirmed`, `remap_approved`, `cancel_job` (`orchestrator.md:65-67`).

---

## 3. PRD REQ-004: "9 step types" → scraper-runner.md

**Estado: CONSISTENTE**

`scraper-runner.md:44-55` tabla "Tipos de step soportados (v1)" lista exactamente 9:

1. `navigate`
2. `click`
3. `fill`
4. `wait_for_selector`
5. `wait_for_download`
6. `select_date_range`
7. `assert_text`
8. `extract_table`
9. `download_file`

Coincide con el PRD. Cada tipo tiene parámetros, success criteria y failure mode documentados. **Sin inconsistencia.**

---

## 4. PRD REQ-010: "7 webhook events" → webhooks.md

**Estado: CONSISTENTE en eventos; INCONSISTENCIA MENOR en retry count vs endpoint approval**

`webhooks.md:9-17` lista exactamente 7 eventos:

1. `job.created`
2. `job.otp_required`
3. `job.progress` (opcional)
4. `job.completed`
5. `job.failed`
6. `job.remap_proposed`
7. `job.human_required`

Coincide con PRD REQ-010 y `DECISIONS.md:64-71`. **Sin inconsistencia en eventos.**

**HMAC format**: El PRD dice `X-OpenBanca-Signature: t=<ts>,v1=<hmac>`. `webhooks.md:54` y `ADR-0011` confirman `X-OpenBanca-Signature: t=<unix_ts>,v1=<hex_hmac>`. **Consistente.**

**Retry policy**: PRD dice "Retry exponential hasta 6 intentos en 24h". `webhooks.md:62-63` confirma "15s, 1m, 5m, 30m, 2h, 6h. Total ~24h, 6 attempts." **Consistente.**

Nota: `orchestrator.md:57` asigna a `EmitWebhookActivity` retry policy "exp backoff, 5 intentos, max 1 h" — **difiere del webhook emitter propio** que hace 6 intentos en 24h. Éstas son capas distintas (la activity de Temporal hace 5 intentos en 1h para entregar al emitter; el emitter propio hace 6 intentos en 24h al cliente HTTP), pero no está explicado en ninguno de los docs que estas son dos políticas de retry independientes. Podría confundir. Sería bueno añadir una nota aclaratoria.

---

## 5. PRD: "3 niveles dedup" → storage.md / parser.md

**Estado: CONSISTENTE**

`storage.md` (sección "Estrategia de dedup (3 niveles, orden estricto)") describe el diagrama y los 3 niveles:

- **Level 1**: embedded `source_id` del banco.
- **Level 2**: fingerprint hash (date + amount + description + account).
- **Level 3**: fuzzy transfer match (vincula, no deduplica).

Coincide con `DECISIONS.md:60-61` y la nota en memoria del proyecto (Banistmo Excel). El Level 3 está correctamente caracterizado como "fuzzy **match** para transfers internas" (no dedup sino linking), lo cual es consistente con la arquitectura.

`storage.md` nota `../06-banks/banco-general.md` para el Level 1. El archivo existe (`docs/06-banks/banco-general.md`). **Sin inconsistencia.**

---

## 6. ADRs vs PRD — contradicciones

### ADR-0006 (Vision split)

**Estado: CONSISTENTE con PRD y DECISIONS.md**

ADR-0006 asigna Claude Sonnet 4.6 a Mapper/Remapper y DeepSeek V3 a Validator/Judge. El PRD y `DECISIONS.md:21-29` son idénticos. Sin contradicción.

Nota de contexto: el ADR menciona `judge-agent.md` como usando DeepSeek "texto" (`ADR-0006:26`) pero `judge-agent.md:13` indica que el screenshot se pasa como "descripción textual generada por OCR/heurístico" porque DeepSeek es texto — esto es consistente y está explicado, pero podría documentarse más explícitamente en `judge-agent.md` que el modelo es texto-only deliberadamente.

### ADR-0007 (DSL Excel)

**Estado: CONSISTENTE con PRD y parser.md**

ADR-0007 describe el DSL declarativo `parser.json` con helpers whitelisted. `parser.md` lo detalla. `scraper-runner.md:104` referencia correctamente `adr/0007-declarative-excel-dsl.md`. Sin contradicción.

### ADR-0013 (Confidence threshold)

**Estado: CONSISTENTE en threshold; INCONSISTENCIA en nombre del endpoint de approval**

El threshold `confidence >= 0.85 AND risk == low` coincide en:
- `ADR-0013:22-27`
- `DECISIONS.md:33-36`
- `judge-agent.md:35-36` (flowchart)
- `webhooks.md:16` (trigger de `job.remap_proposed` si `confidence < 0.85`)

**INCONSISTENCIA en endpoint de approval**:

- `ADR-0013:27` menciona `POST /jobs/{id}/approve-remap` como el endpoint de approval.
- `api.md:35-36` y `api.md:71-76` definen el endpoint real como `POST /maps/{bank}/proposals/{id}/approve`.

El endpoint en ADR-0013 es incorrecto o corresponde a un diseño anterior. El path correcto requiere `bank_id` y `proposal_id`, no sólo `job_id`. **Acción requerida**: corregir `ADR-0013:27`.

---

## 7. Cross-references — links rotos o referencias obsoletas

### Links rotos identificados

1. **`scraper-runner.md:103`**: `06-banks/banco_general/` (con guión bajo) referenciado como "a redactar". El archivo existente es `docs/06-banks/banco-general.md` (con guión). Si se crea un directorio `banco_general/` separado con `map.json`, no hay conflicto, pero la convención de nombres es inconsistente con `banco-general.md`.

2. **`ADR-0013:27`**: `POST /jobs/{id}/approve-remap` — endpoint no existe en `api.md`. El endpoint correcto es `POST /maps/{bank}/proposals/{id}/approve`.

3. **`webhooks.md:64`**: menciona `GET /webhooks/dlq` y `POST /webhooks/dlq/:id/replay` como endpoints operacionales del DLQ, pero estos paths no aparecen en `api.md`. Si son endpoints reales deben estar en la tabla de api.md (aunque sea como sección de admin endpoints separada).

### Links válidos verificados

- `scraper-runner.md` → `03-flows/remap-detection.md` ✓ (archivo existe)
- `scraper-runner.md` → `03-flows/error-recovery.md` ✓ (archivo existe)
- `storage.md` → `../06-banks/banco-general.md` ✓ (archivo existe)
- `parser.md` → `../adr/0007-declarative-excel-dsl.md` ✓
- `judge-agent.md` → `../adr/0013-confidence-threshold-remap.md` ✓
- `api.md` → `../adr/0011-webhook-events-hmac.md` ✓
- `api.md` → `../03-flows/remap-approval.md` ✓
- `orchestrator.md` → `../adr/0003-temporal-orchestration.md` ✓

---

## 8. DECISIONS.md vs ADRs — consolidación

**Estado: BIEN CONSOLIDADO, con un gap menor**

`DECISIONS.md` cubre todos los 15 ADRs con referencias explícitas a:
- ADR-0001 (mapper-runner split)
- ADR-0002 (excel download strategy)
- ADR-0003 (Temporal)
- ADR-0004 (multi-agent)
- ADR-0005 (PydanticAI + LiteLLM)
- ADR-0006 (vision split)
- ADR-0007 (DSL excel)
- ADR-0008 (sqlcipher secrets)
- ADR-0009 (docker sandbox)
- ADR-0011 (webhook HMAC)
- ADR-0012 (schema canónico)
- ADR-0013 (confidence threshold)
- ADR-0014 (browser-use)
- ADR-0015 (no session persistence)

**Gap**: **ADR-0010 (AGPL-3.0)** no tiene sección propia en `DECISIONS.md`. La licencia es mencionada en la intro del documento como hecho, pero no hay una entrada en el registro de decisiones que explique el razonamiento (que sí está en ADR-0010). Menor pero incompleto como registro.

`DECISIONS.md` está actualizado al mismo nivel de decisión que los ADRs en todos los otros puntos. Los valores de threshold (0.85, 3 remaps/24h, $0.50/job, 2 logins → circuit breaker) coinciden entre `DECISIONS.md:93-97` y `ADR-0013`, `05-operations/cost-guardrails.md`.

---

## 9. Gaps de documentación

### Componentes en PRD sin doc de componente dedicado (pero con doc en 05-operations/)

Los tres componentes señalados en el PRD como "Phase 5" sí tienen docs en `05-operations/`:

| Componente | Archivo | Estado |
|------------|---------|--------|
| Sandbox Docker per-job | `04-security/sandbox.md` | ✓ existe |
| Cost guardrails | `05-operations/cost-guardrails.md` | ✓ existe |
| Observability (Langfuse + OTel) | `05-operations/observability.md` | ✓ existe |

**No hay gaps críticos** en los temas señalados. Sin embargo, no existen docs de componente en `02-components/` para sandbox, cost-guardrails y observability — son sólo docs de operaciones. Esto es una decisión de organización, no un gap, pero las referencias cruzadas desde `judge-agent.md:125` apuntan correctamente a `05-operations/cost-guardrails.md`.

### Gaps reales identificados

1. **`02-components/` sin doc para el componente Remapper Agent**: hay `mapper-agent.md` pero no `remapper-agent.md`. El Remapper es el quinto agente nombrado en la arquitectura (PRD, DECISIONS.md, CLAUDE.md). `mapper-agent.md` incluye una sección de Remapper al final pero no hay archivo dedicado. Si Remapper tiene suficiente complejidad propia (scope diff, dry-run path), merece su propio archivo o el doc compartido debería renombrarse.

2. **`map.json` schema no documentado**: `scraper-runner.md:103` apunta a `06-banks/banco_general/` como "a redactar". El formato de `map.json` es central al sistema (es lo que genera Mapper y ejecuta Runner) pero no tiene spec formal. ADR-0007 documenta `parser.json` (Excel DSL) pero no `map.json` (scraper DSL). Hay una nota en `docs/00-overview.md` o `CLAUDE.md` implícita pero no un documento con el schema de `map.json`.

3. **`GET /time` endpoint**: el PRD REQ-010 menciona `Endpoint /time para diagnosis clock skew` pero este endpoint no aparece en `api.md` (ni en la tabla de 10 endpoints, ni en secciones adicionales). Si es un endpoint real del sistema debería agregarse a `api.md` (lo cual también cambiaría el conteo de 10 a 11).

---

## Resumen de hallazgos por severidad

### Alta (requiere corrección)

| ID | Archivo:línea | Descripción |
|----|--------------|-------------|
| H1 | `adr/0013-confidence-threshold-remap.md:27` | Endpoint de approval incorrecto: dice `POST /jobs/{id}/approve-remap`, debe ser `POST /maps/{bank}/proposals/{id}/approve` |
| H2 | `docs/02-components/orchestrator.md` tabla vs PRD REQ-002 | PRD dice "11 activities", doc lista 10. Falta identificar/documentar la undécima, o corregir el PRD a 10. Candidato: activity de commit/storage |

### Media (inconsistencia funcional, no bloqueante)

| ID | Archivo:línea | Descripción |
|----|--------------|-------------|
| M1 | `webhooks.md:64` vs `api.md:26-39` | `GET /webhooks/dlq` y `POST /webhooks/dlq/:id/replay` mencionados en webhooks.md pero ausentes en tabla de endpoints de api.md |
| M2 | `orchestrator.md:57` vs `webhooks.md:62-63` | `EmitWebhookActivity` retry policy "5 intentos, max 1h" vs webhook emitter "6 intentos, 24h" — son capas distintas pero no está explicado en ningún doc, puede confundir |
| M3 | PRD REQ-010 | `Endpoint /time para diagnosis clock skew` mencionado en PRD pero ausente en `api.md`. Si es real, falta en la tabla de endpoints (y el conteo sería 11, no 10) |

### Baja (mejoras de completitud)

| ID | Archivo:línea | Descripción |
|----|--------------|-------------|
| L1 | `scraper-runner.md:103` | Referencia a `06-banks/banco_general/` (guión bajo) inconsistente con convención `banco-general` (guión). Marcado "a redactar" |
| L2 | `DECISIONS.md` | ADR-0010 (AGPL-3.0) no tiene entrada en el registro de decisiones |
| L3 | `docs/02-components/` | No existe `remapper-agent.md` dedicado. Remapper está subsumido en `mapper-agent.md` |
| L4 | `docs/` | Falta spec de `map.json` schema. ADR-0007 documenta el DSL de `parser.json` pero no existe ADR equivalente para el formato de `map.json` del scraper |
| L5 | `judge-agent.md:13` | Nota que el screenshot se convierte a descripción textual para DeepSeek, pero no referencia ADR-0006 que explica por qué Judge es texto-only |
