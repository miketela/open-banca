# Master Feedback — open-banca v1

**Fecha:** 2026-05-10
**Reviewers:** 8 agentes (2 Opus thinking + 6 Sonnet)
**Scope:** PRD + 4 docs arquitectura + 15 ADRs + 10 docs componentes + 6 flows + 4 docs seguridad + 4 ops + banco-general + tasks.json
**Estado código:** sin código aún (solo specs + tasks)

---

## TL;DR

Specs sólidas y bien estructuradas, pero **6 bloqueantes P0 deben cerrarse antes de Task #1 del taskmaster**. Costo estimado: 2–4 días de trabajo de specs + 1–2 días de spikes con creds reales. Sin esto, Phase 3 piloto falla con alta probabilidad o termina con la cuenta bancaria del operador bloqueada.

---

## P0 — Bloqueantes pre-Phase 1

### P0-1. Browser context cross-restart durante OTP (Architecture)
**Fuente:** `01-architecture-opus.md` RC1.
**Problema:** ADR-0003 + ADR-0015 + `otp-pause-resume.md:80-95` asumen que el navegador sigue vivo si el worker Temporal muere durante el wait OTP. Nadie mantiene la conexión CDP. Actualmente no es ejecutable.
**Acción:** ADR nuevo (0019) — `BrowserSidecar` proceso aparte que sobrevive al worker, o aceptar abort-on-crash y documentarlo en flow.

### P0-2. Master passphrase sin mlock + core dumps (Security)
**Fuente:** `02-security-opus.md` P0-2.
**Problema:** Passphrase en heap Python, swap habilitado, sin `ulimit -c 0`. Colapsa toda la cadena defense-in-depth.
**Acción:** enforce `mlock` via `cryptography.hazmat` o C-extension wrapper, `ulimit -c 0` en docker-compose, deshabilitar swap explícitamente. Documentar en `secrets-at-rest.md` como hard requirement.

### P0-3. PII del usuario fluye al LLM provider — Ley 81 PA (Security)
**Fuente:** `02-security-opus.md` P0-1.
**Problema:** browser-use `sensitive_data` solo redacta credenciales que tú declaras. Post-login el banco renderiza nombre, número de cuenta, balance, etc. Todo eso llega al provider (Anthropic US o DeepSeek CN). Ley 81 PA Art. 13 exige consentimiento explícito para transferencia transfronteriza.
**Acción:** ADR nuevo + filter middleware que redacte PII en screenshots/DOM antes de mandar al LLM, o restringir provider a regiones permitidas. Disclosure en docs operacionales.

### P0-4. Parser DSL: ReDoS, zip-bomb, lookup_table DoS (Security)
**Fuente:** `02-security-opus.md` P0-5.
**Problema:** `extract_regex` usa `re` (vulnerable a ReDoS), `openpyxl` sin defusedxml (CVE-2017-5992 zip-bomb), `lookup_table` sin budget de memoria.
**Acción:** swap a `re2`, `defusedxml.lxml` para Excel, budget de memoria/tiempo por helper. Tests con payloads maliciosos en CI.

### P0-5. REQ-018 (docker-socket-proxy) está en P2 — debe ser P0
**Fuente:** `02-security-opus.md` P0-4 + `04-tasks-coverage-sonnet.md`.
**Problema:** Phase 3 piloto correrá con socket exposure. Cualquier CVE de Chromium = host pwn. Inconsistencia con T14 (sandbox) marcado crítico.
**Acción:** Promover REQ-018 a P0 en PRD. Adelantar T24 a Phase 1.

### P0-6. Auto-apply de remap con Judge text-only sobre OCR (Architecture)
**Fuente:** `01-architecture-opus.md` RC2 + `06-banco-general-sonnet.md` BLK-3.
**Problema:** ADR-0013 confía en `confidence` de DeepSeek **texto** sobre evidencia textualizada de un screenshot via OCR. Calibración admitida como "arbitraria". Loop circular.
**Acción mínima v1:** HITL-only — eliminar auto-apply hasta tener data calibrada. **Alternativa:** Judge-vision (Claude) en breakage path, asumir el costo.

---

## P1 — Altos (cerrar en Phase 1, no bloquean arranque)

| ID | Categoría | Resumen | Acción |
|----|-----------|---------|--------|
| P1-1 | Architecture | Mapper→Runner sin `mapper_self_test` antes de `done()`. Widgets enum incorrectos solo detectados en prod. | Añadir self-test obligatorio al Mapper agent. |
| P1-2 | Architecture | Modelo de partial completion (cuenta A persiste, B falla) no documentado. | ADR nuevo (0016). |
| P1-3 | Architecture | Stack LLM sin fallback declarado. ADR-0006 referencia "ADR de fallbacks" inexistente. | ADR nuevo (0018). |
| P1-4 | PRD | Goal 1 (success rate) depende de Langfuse (P1) — ciclo. | Mover instrumentación mínima a P0 (counter SQLite). |
| P1-5 | PRD | NFR `p99 < 300s` incoherente con OTP cap 240s si se mide end-to-end. | Definir 2 SLOs: `interactive_p99` (excluye OTP) + `wallclock_p99` (incluye). |
| P1-6 | PRD | REQ-019 (sensitive_data fuzz) en P2 pero Goal 5 es zero-tolerance. | Promover a P0. |
| P1-7 | PRD | OTP timeout sin AC ni state machine. Wrong creds / account lockout sin story. | Añadir Story 6 + ACs. |
| P1-8 | Banco General | 3 bloqueantes Phase 3: ID estable transacciones, rango histórico real, sesión simultánea. | 5 spikes con creds reales en Phase 1 (2 días). |
| P1-9 | Banco General | Account block risk: circuit breaker no distingue `credential_error` vs `push_rejected`. | Threshold 1 fallo de cred + clasificación tipo de fallo. |
| P1-10 | Banco General | Push Clave Móvil sin fallback si nunca llega. | SPIKE-4: confirmar si banco tiene OTP código alternativo. |
| P1-11 | Tasks | T3 son stubs eternos — falta task "wire FastAPI endpoints to use cases". | Añadir task. |
| P1-12 | Tasks | Falta task cursor `since` + incremental params (Story 2 AC). | Añadir task. |
| P1-13 | Tasks | Falta docker-compose full stack (Story 5 AC). | Añadir task. |
| P1-14 | Tasks | Falta CLI `open-banca register-credentials`. | Añadir task. |
| P1-15 | Tasks | `estimatedHours` ausente en las 30 tasks. | Re-run taskmaster con hint. |
| P1-16 | Testing | Self-healing E2E (BreakageEvent→Judge→Remapper→retry) solo manual (Checkpoint 4). | Test automatizado con FunctionModel. |
| P1-17 | Testing | Canary no cubre 3 sinks: Temporal history, screenshots binarios, webhook payloads. | Extender canary a esos 3. |
| P1-18 | Testing | HAR sanitizer es spec, no módulo implementado. Response bodies no inspeccionados. | Implementar + commit hook. |
| P1-19 | Cost | Mapping $0.50 cap solo alcanzable en path optimista (DOM-first + cache). Normal: $0.80–$1.50. | Subir cap a $1.00 o documentar abort frecuente esperado. |
| P1-20 | Docs | ADR-0013:27 endpoint approval incorrecto vs api.md. | Fix ADR. |
| P1-21 | Docs | REQ-002 dice "11 activities", orchestrator.md tabla lista 10. | Reconciliar. |
| P1-22 | Webhooks | Window ±5min excesivo. `/time` endpoint = side-channel oracle. | 60s + nonce required (no opcional). Considerar Ed25519. |

---

## P2 — Medios (Phase 2+ aceptable)

- Threat model amplía: 9 amenazas nuevas (T18–T26) — PII al LLM, swap dump, supply chain, ReDoS, OTP storage, API token, time oracle, GitHub OIDC compromise, cross-border PII.
- Confidence/risk boundary tests faltantes (`>= 0.85 AND risk == low`).
- Cap 3 remap/24h + TTL 24h proposals sin tests.
- Sandbox network egress sin spec de qué se verifica.
- `hypothesis` faltante en HMAC verification, Excel malformado, Argon2id roundtrip.
- DLQ endpoints (`GET /webhooks/dlq`, replay) ausentes en `api.md`.
- `/time` endpoint ausente de api.md.
- Anthropic Tier requirement (mín. Tier 2) no documentado.
- Hardware mínimo no documentado (estimación: 4 vCPU / 8 GB / 50 GB SSD).
- Artifact storage backend (~5 GB/día) sin definir.
- 6 ADRs a parchear: 0001, 0003, 0006, 0013, 0014, 0015.
- 4 ADRs a crear: 0016 (partial completion), 0017 (dedup formal), 0018 (LLM fallback), 0019 (browser sidecar).
- Phase distribution: tasks.json -13 tasks vs PRD objetivo. Phase 6 tiene 1 vs 5.
- USER-TEST checkpoint 6 falta como task dedicada.
- T&C de Banco General no revisados — riesgo legal/account suspension.

---

## Plan de acción recomendado

**Día 1–2 (specs):**
- Cerrar P0-1, P0-2, P0-4, P0-5, P0-6 con ADRs nuevos/parches.
- Reclasificar REQ-018, REQ-019 a P0. Reordenar tasks.
- Reconciliar inconsistencias docs (ADR-0013, REQ-002).
- Ampliar threat model con 9 amenazas nuevas.

**Día 3–4 (spikes con creds reales operador):**
- SPIKE-1: ¿hay ID estable en Excel Banco General?
- SPIKE-2: formato real TC vs savings.
- SPIKE-3: HAR — ¿hay API JSON detrás del portal?
- SPIKE-4: mecanismo real del push polling + fallback código.
- SPIKE-5: selectores menú de cuentas.
- Bonus: revisar T&C de Banco General sobre automation.

**Día 5+:** arrancar Task #1 del taskmaster.

**Coste estimado de NO hacer esto:** Phase 3 falla, cuenta bancaria operador bloqueada, refactor caro post-código.

---

## Reportes individuales

| # | Tema | Modelo | Archivo |
|---|------|--------|---------|
| 1 | Architecture | Opus | `01-architecture-opus.md` |
| 2 | Security | Opus | `02-security-opus.md` |
| 3 | PRD quality | Sonnet | `03-prd-quality-sonnet.md` |
| 4 | Tasks coverage | Sonnet | `04-tasks-coverage-sonnet.md` |
| 5 | Docs consistency | Sonnet | `05-docs-consistency-sonnet.md` |
| 6 | Banco General | Sonnet | `06-banco-general-sonnet.md` |
| 7 | Performance + Cost | Sonnet | `07-perf-cost-sonnet.md` |
| 8 | Testing | Sonnet | `08-testing-sonnet.md` |

---

## Métricas de la review

- 8 agentes (2 Opus + 6 Sonnet) en paralelo.
- 6 P0 bloqueantes, 22 P1 altos, ~30 P2 medios.
- ~180 KB de reportes detallados.
- 0 código revisado (no existe aún) — review de specs/PRD/tasks puro.
