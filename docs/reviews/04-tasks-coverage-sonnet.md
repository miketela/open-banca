# Review: Cobertura de Tasks vs PRD — open-banca v1

**Fecha:** 2026-05-10  
**Modelo:** claude-sonnet-4-6  
**Fuentes analizadas:** `.taskmaster/docs/prd.txt`, `.taskmaster/tasks/tasks.json` (30 tasks)

---

## 1. Matriz REQ → Tasks

| REQ | Título | Tasks | Cobertura |
|-----|--------|-------|-----------|
| REQ-001 | API REST FastAPI | T3 | PARCIAL — T3 son stubs 501; no hay task de implementación real |
| REQ-002 | Temporal orquestador | T12 | PARCIAL — skeleton de actividades; OTPSignalAwaitActivity sin task propia |
| REQ-003 | Mapper agent | T14 | OK |
| REQ-004 | Scraper runner Playwright | T9 | OK |
| REQ-005 | Validator + Judge agents | T19 | OK |
| REQ-006 | Remapper agent | T20 | OK |
| REQ-007 | Excel parser DSL | T15 | OK |
| REQ-008 | Storage SQLite + sqlcipher | T2, T4, T17 | OK |
| REQ-009 | Sandbox Docker per-job | T23 | OK |
| REQ-010 | Webhooks HMAC-SHA256 | T25 | OK |
| REQ-011 | Cost guardrails | T26 | OK |
| REQ-012 | Banco General piloto E2E | T16, T30 | OK |
| REQ-013 | Langfuse self-hosted opcional | T27 | OK |
| REQ-014 | OTel para resto | T27 | OK (mismo task, puede ser denso) |
| REQ-015 | Testing infrastructure HAR | T11 | OK |
| REQ-016 | Filter middleware redact + canary CI | T5, T7 | OK |
| REQ-017 | Community maps trust model | T28 | OK |
| REQ-018 | docker-socket-proxy hardening | T24 | OK |
| REQ-019 | sensitive_data fuzz test | T14 | PARCIAL — cubierto en testStrategy de T14, no task dedicada |
| REQ-020 | Webhook clock skew /time endpoint | T25 | OK (dentro de T25 junto con 3 otras features) |

### REQs sin cobertura completa

- **REQ-001**: T3 crea stubs 501 para los 10 endpoints. **No existe task que implemente los endpoints reales** (StartScrapeJob conectado a Temporal, OTP flow activo, resultados reales). Los endpoints quedan como 501 indefinidamente a menos que el implementador asuma que T12/T9 los conectan — pero no hay task explícita de "wire endpoints to use cases".
- **REQ-002**: El skeleton de T12 declara 11 actividades pero `OTPSignalAwaitActivity` y los 3 signals (`otp_confirmed`, `remap_approved`, `cancel_job`) no tienen task de implementación independiente. T21 cubre el lado API del HITL, pero el signal side de Temporal es implícito.
- **REQ-019**: El fuzz test de `sensitive_data` aparece en el `testStrategy` de T14, no como task separada. Si T14 se cierra sin implementar el fuzz, el AC queda sin verificar.

---

## 2. User Stories — Cobertura End-to-End

| Story | Descripción | Tasks que la cubren | Gaps |
|-------|-------------|---------------------|------|
| Story 1 | Primer scrape banco recién agregado | T2, T6, T9, T12, T14, T16, T18 | Falta task que conecte FastAPI → Temporal (endpoints no son stubs) |
| Story 2 | Scrape incremental periódico (`since` cursor) | T4, T15, T17, T18 | T17 cubre dedup 3-level, pero el cursor `since` y la ventana `since-3d` no tienen task explícita. T4 almacena datos pero no hay task de "cursor storage + API params" (hint del PRD: 4h) |
| Story 3 | Self-healing cuando el banco cambia | T10, T12, T19, T20, T21, T22 | OK — pipeline completo cubierto |
| Story 4 | Cuentas múltiples por login | T2, T15, T16, T17 | El campo `detect-all` y los campos TC (`credit_limit`, `cut_date`, etc.) están en T17 schema, pero no hay task explícita de "multi-account navigator" en el scraper runner |
| Story 5 | Operador hace setup en 30 min | T1, T6, T27, T29, T30 | **Faltan:** CLI `open-banca register-credentials`, docker-compose full-stack (T6 es solo Temporal dev), README + deployment.md |

### Gaps críticos Story 2 y Story 5

**Story 2 — cursor `since`**: El PRD breakdown hint es explícito ("Cursor storage + API params 4h"). No existe task para esto. El cursor puede quedar sin implementar si no se expande T4 o T17.

**Story 5 — setup 30 min**: Los 3 ACs siguientes no tienen task:
1. `docker-compose.yml` full (api + temporal-worker + sandbox-runner + postgres). T6 solo levanta Temporal dev; T24 menciona docker-compose para socket-proxy pero no el stack completo.
2. CLI `open-banca register-credentials --bank banco_general`.
3. `docs/05-operations/deployment.md` con paso a paso.

---

## 3. Análisis de Dependencias

### Sin ciclos ni dependencias rotas

DFS sobre el grafo confirma: **0 ciclos, 0 referencias a tasks inexistentes**.

### Dependencias faltantes (no bloquean construcción pero crean riesgo de testing)

| Task | Dep faltante | Problema |
|------|-------------|---------|
| T14 (Mapper agent) | T11 (HAR infra) | T14 se puede implementar y "pasar" sin fixtures HAR; el `testStrategy` del sensitive_data fuzz queda sin infraestructura |
| T19 (Validator/Judge) | T11 (HAR infra) | Mismo problema: tests necesitan replay HAR para simular breakages sin banco real |
| T25 (Webhooks) | T3 (FastAPI) | El endpoint `/webhooks/test` (declarado en T3) debería existir antes de T25; actualmente T25 puede completarse sin validar integración con API |
| T27 (Observabilidad) | T7 (CI pipeline) | OTel exporters no se validan en CI si T27 no depende de T7 |
| T30 (Release) | T27 (Observabilidad) | Release gate no exige observabilidad lista; T30 depende de T29 que sí depende de T27, así que es **transitivo** — OK en la práctica |
| T30 (Release) | T28 (Community maps) | Ídem — transitivo via T29 — OK |

### Dependencia suspiciosamente ausente más relevante

**T14 → T11**: El Mapper agent es el componente más riesgoso del proyecto (LLM + browser + credenciales). Sin HAR replay, cada test del Mapper requiere golpear al banco real. Agregar `T11` como dependencia de `T14` fuerza que la infraestructura de test exista antes.

---

## 4. Estimaciones

### Campo `estimatedHours` ausente en las 30 tasks

El campo `estimatedHours` **no existe en ninguna task** del JSON. El PRD especifica "4–8h por task atómica" como convención, pero Taskmaster no lo generó. Esto impide planificación de sprints y detección de tasks sobreestimadas via campo.

### Evaluación heurística por complejidad de descripción

| Task | Estimado realista | Problema |
|------|-------------------|---------|
| T9 — Scraper runner 9 step types | 12–16h | Debería dividirse: 4 step types básicos (T9a) + 5 avanzados + date selectors (T9b) |
| T12 — ScrapeJobWorkflow + 11 activities skeleton | 12–16h | "Skeleton" puede parecer simple pero el contrato de cada activity + Temporal signals son 11 interfaces distintas |
| T14 — Mapper agent (browser-use + Claude vision + sensitive_data) | 12–16h | 3 subsistemas integrados; el fuzz test de sensitive_data es trabajo adicional |
| T17 — Schema canónico + 3-level dedup + fuzzy transfer matching | 12–16h | El PRD breakdown hint para Story 2 asigna 12h solo al dedup engine. T17 también incluye schema y fuzzy matching. |
| T5 — Vault + filter middleware (logs+OTel+Langfuse+HAR) | 8–12h | 4 interceptores distintos (LogRecord, OTel spans, Langfuse traces, HAR sanitizer) |
| T25 — Webhooks + retry + DLQ + /time endpoint | 8–12h | 4 features en 1 task; puede romperse en T25a (HMAC+retry) + T25b (DLQ+/time) |
| T27 — Langfuse + OTel (dos REQs: REQ-013 + REQ-014) | 8–12h | Cubre 2 REQs con contextos distintos |
| T6 — Temporal local docker-compose | 3–4h | Posiblemente sub-estimado si se considera el worker scaffolding con 11 activity placeholders |
| T8, T13, T18, T22, T29 — USER-TEST checkpoints | 1–2h cada uno | Checkpoints son demos, no implementación |
| T30 — Release v1.0.0 | 8–16h | Betatesters reales + stress 100 scrapes + docs completas + AGPL headers |

**Tasks candidatas a split (>8h estimado realista):** T9, T12, T14, T17, T25, T27.

---

## 5. USER-TEST Checkpoints — Posicionamiento

| Checkpoint | Task | Post-Phase | Deps declaradas | Evaluación |
|------------|------|------------|-----------------|------------|
| CP1 | T8 | Phase 1 | T3, T4, T5, T6, T7 | **INCOMPLETO**: T2 no es dep directa de T8. Si T2 falla, T3/T4 pasan igual en tests unitarios pero el scaffold hexagonal podría estar roto. Agregar T2 a deps. |
| CP2 | T13 | Phase 2 | T11, T12 | **INCOMPLETO**: T9 y T10 no son deps directas. T12 depende de T9 y T10 (transitivo), pero el checkpoint debería validar explícitamente T9 (9 step types) y T10 (BreakageEvent). |
| CP3 | T18 | Phase 3 | T16, T17 | **INCOMPLETO**: T14 y T15 no son deps directas (T16 depende de T14+T15, transitivo). OK transitivamente pero si T14/T15 tienen deuda técnica, T18 no la detecta. |
| CP4 | T22 | Phase 4 | T21 | **INCOMPLETO**: Solo depende de T21 (HITL endpoints). No valida T19 (Validator/Judge) ni T20 (Remapper) directamente. T21→T20→T19 es transitivo, pero el checkpoint podría agregar T19 para forzar la demo de confidence/risk. |
| CP5 | T29 | Phase 5 | T23, T24, T25, T26, T27, T28 | **OK** — cubre todas las tasks de Phase 5. |

**Checkpoint 6 (release)**: El PRD menciona 6 checkpoints pero tasks.json solo tiene 5 USER-TEST tasks. T30 es el release, no un USER-TEST checkpoint con rollback explícito. Falta `USER-TEST checkpoint 6: v1.0.0 release gate`.

---

## 6. Cobertura de Test Strategy

**Todas las 30 tasks tienen `testStrategy` no vacío** — cobertura completa a nivel campo.

### Quality concerns

| Task | Concern |
|------|---------|
| T8, T13, T18, T22, T29 | testStrategy es "Demo en vivo" — válido para checkpoints pero no es test automatizable. Debería incluir checklist de criterios binarios pass/fail para evitar subjetividad. |
| T16 | testStrategy depende de banco real; no hay fallback si banco no está disponible durante CI. |
| T28 | testStrategy menciona cosign keyless pero no especifica qué artefacto se firma ni cómo verificar la firma en CI. |
| T30 | testStrategy incluye "Si success rate < 95%, no release" pero no especifica quién toma la decisión ni el proceso de escalation. |

---

## 7. Cobertura de GitHub Issues

| Issue | Descripción PRD | Task | Cobertura |
|-------|----------------|------|-----------|
| #1 | Filter redact + canary CI test | T5 (implementación), T7 (CI) | **OK** — doble cobertura: vault en T5, canary en CI en T7 |
| #2 | docker-socket-proxy hardening | T24 | **OK** — task dedicada con smoke tests |
| #3 | sensitive_data fuzz test (browser-use) | T14 (testStrategy) | **PARCIAL** — fuzz test está en testStrategy de T14, no como task/subtask independiente. Si T14 se cierra sin el fuzz, AC queda sin verificar. Recomendado: subtask explícita o task T14b. |
| #4 | cosign keyless signing (community maps) | T28 | **PARCIAL** — T28 menciona "cosign keyless" en título pero `testStrategy` no especifica el artefacto a firmar ni el comando de verificación. |
| #5 | /time endpoint clock skew | T25 | **OK** — incluido en título y testStrategy de T25 |

---

## 8. Distribución por Phase

| Phase | PRD target | tasks.json actual | Delta | Tasks |
|-------|-----------|-------------------|-------|-------|
| Phase 1 | 8 | 8 | 0 | T1–T8 |
| Phase 2 | 6 | 5 | **-1** | T9–T13 |
| Phase 3 | 9 | 5 | **-4** | T14–T18 |
| Phase 4 | 7 | 4 | **-3** | T19–T22 |
| Phase 5 | 8 | 7 | **-1** | T23–T29 |
| Phase 6 | 5 | 1 | **-4** | T30 |
| **Total** | **43** | **30** | **-13** | |

El PRD sugiere 40–50 tasks principales. El set actual tiene 30, lo que es consistente con el mínimo del rango pero **las phases 3, 4 y 6 están significativamente por debajo** del objetivo del PRD.

### Tasks faltantes inferidas por phase

**Phase 3 (falta ~4 tasks):**
1. `cursor since storage + API param` — Story 2 AC explícito, hint en PRD = 4h
2. `docker-compose full stack` (api + worker + sandbox + postgres) — Story 5 AC
3. `multi-account navigator` en scraper runner — Story 4 (detect-all, campos TC)
4. `Wire FastAPI endpoints to use cases` — REQ-001 AC (T3 son 501 stubs)

**Phase 4 (falta ~3 tasks):**
1. `OTPSignalAwaitActivity + signal handlers` — REQ-002, T12 skeleton no lo implementa
2. Puede ser que T19 y T20 deberían dividirse (Validator separado de Judge)

**Phase 6 (falta ~4 tasks):**
1. `USER-TEST checkpoint 6: release gate` — PRD menciona 6 checkpoints
2. `CLI open-banca register-credentials` — Story 5 AC
3. `README + docs/05-operations/deployment.md` — Story 5 AC
4. `AGPL-3.0 headers + license audit` — T30 lo menciona pero debería ser task propia (T30 ya es densa)

---

## 9. Resumen Ejecutivo de Gaps

### Críticos (bloquean ACs del PRD)

| # | Gap | REQ/Story afectada | Acción sugerida |
|---|-----|-------------------|-----------------|
| G1 | No hay task para implementar endpoints FastAPI reales (T3 = stubs 501) | REQ-001, Story 1, Story 5 | Agregar T31: "FastAPI endpoints: wire to use cases" en Phase 3/4 |
| G2 | `since` cursor + API param para scrape incremental | Story 2 AC | Agregar T32: "Cursor since storage + incremental scrape params" en Phase 3 |
| G3 | docker-compose full stack (no solo Temporal dev) | Story 5 AC | Agregar T33: "docker-compose production stack" en Phase 5/6 |
| G4 | CLI `open-banca register-credentials` | Story 5 AC | Agregar T34: "CLI register-credentials interactive" en Phase 6 |
| G5 | `estimatedHours` ausente en las 30 tasks | Convención PRD | Ejecutar `task-master update` o setear manualmente vía `set_task_status` |

### Importantes (degradan calidad pero no bloquean ACs P0)

| # | Gap | Acción sugerida |
|---|-----|-----------------|
| G6 | sensitive_data fuzz (Issue #3) solo en testStrategy de T14 | Convertir en subtask explícita de T14 |
| G7 | T14 no depende de T11 (HAR infra) — tests del Mapper sin fixtures | Agregar dep T14→T11 |
| G8 | T19 no depende de T11 — tests del Validator/Judge sin replay HAR | Agregar dep T19→T11 |
| G9 | T8 (CP1) no tiene T2 como dep directa | Agregar T2 a deps de T8 |
| G10 | T22 (CP4) no valida T19/T20 directamente | Agregar T19, T20 a deps de T22 |
| G11 | Tasks sobredensas T9, T12, T14, T17, T25 exceden 8h realistas | Considerar split antes de expand |
| G12 | Checkpoint 6 (release gate) no tiene USER-TEST task dedicada | Agregar T35: "USER-TEST checkpoint 6: release gate" |

---

*Generado por claude-sonnet-4-6. Revisión basada en análisis estático del JSON — no ejecuta código del proyecto.*
