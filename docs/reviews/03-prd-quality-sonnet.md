# PRD Quality Review: open-banca v1

**Reviewer:** Claude Sonnet 4.6 (code review agent)
**Date:** 2026-05-10
**PRD Version:** 1.0 (Date: 2026-05-09, Status: Approved)
**Scope:** Contractual quality — testability, measurability, completeness, consistency

---

## Summary

The PRD is structurally mature: goals have metrics, stories have ACs, requirements have priorities. However there are **4 blocking issues** that would cause implementation ambiguity or unmeasurable outcomes in Phase 1–2, plus several non-blocking gaps worth addressing before development begins.

Severity legend: **[BLOCKER]** = stops Phase 1/2 work. **[MAJOR]** = causes rework post-implementation. **[MINOR]** = clarification or polish.

---

## 1. Acceptance Criteria That Are Not Objectively Testable

### AC-1.1 [BLOCKER] "Mapper agent corre automáticamente" — prd.txt:109
```
- [ ] Si no existe `map.json` para Banco General, Mapper agent corre automáticamente.
```
**Problem:** "Corre automáticamente" is not a testable observable outcome. The AC does not specify: (a) within what timeframe does Mapper complete before the original scrape job proceeds? (b) is a failed Mapper a fatal job failure or a graceful fallback? (c) does `job.progress` emit a distinct event for "mapping in progress"?

**Fix needed:** Add observable assertions — e.g., `job.mapping_started` webhook emitted within Xs; `map.json` persisted to storage before scrape step begins; if Mapper fails, job transitions to `job.failed` with `reason: mapper_failed`.

---

### AC-1.2 [MAJOR] "Parser DSL convierte Excel → schema canónico unificado" — prd.txt:113
```
- [ ] Parser DSL convierte Excel → schema canónico unificado.
```
**Problem:** "Schema canónico unificado" is not testable without a canonical schema definition in this document. What fields are mandatory? What is the type/format of `amount`, `date`, `description`? What happens when a field is missing from the Excel?

**Fix needed:** Reference the canonical schema (presumably in `docs/02-components/parser.md`) explicitly in this AC, or include a minimal JSON Schema inline. Otherwise the acceptance test has no oracle.

---

### AC-1.3 [MAJOR] "Result incluye: cuentas, balances, transacciones (deduplicadas)" — prd.txt:115
```
- [ ] Result incluye: cuentas, balances, transacciones (deduplicadas).
```
**Problem:** "Deduplicadas" is subjective without specifying: which dedup level applies here (ID-level, hash-level, transfer-matching)? The dedup engine has 3 levels (prd.txt:139) but Story 1's AC does not state which apply to the initial full scrape vs. incremental.

---

### AC-2.1 [MAJOR] Story 2 — "Result devuelve sólo registros nuevos" — prd.txt:141
```
- [ ] Result devuelve sólo registros nuevos.
```
**Problem:** Not testable without defining "nuevo" relative to the cursor. Edge case: if `since - 3 días` buffer (prd.txt:138) overlaps with previously returned records, are those "nuevos"? The buffer implies some overlap is expected and must be deduped — but the AC says "solo registros nuevos". These are contradictory without a precise definition.

---

### AC-3.1 [BLOCKER] "Confidence ≥ 0.85 AND risk == low → auto-apply remap" — prd.txt:162 and REQ-005:248
```
- [ ] Si `confidence ≥ 0.85 AND risk == low`, Remapper corre, parche se aplica, job se reintenta.
```
**Problem:** `confidence` is a float produced by an LLM (DeepSeek). How is this score validated in a test harness? The test cannot mock an LLM to reliably produce `confidence = 0.85`. The AC is only testable if the Judge agent is injectable/mockable and the contract specifies the exact JSON response schema (including valid `risk` enum values). The PRD does not define the Judge output schema.

**Fix needed:** Define Judge output schema (fields, types, valid enum values for `risk`) as a formal contract. Reference it in REQ-005 AC.

---

### AC-4.1 [MINOR] Story 4 — "Cada cuenta tiene su propia descarga Excel (formato puede diferir entre savings y TC)" — prd.txt:189
This is a design note, not an AC. Replace with: "For each account type (savings, checking, credit_card), a distinct Excel file is downloaded and parsed without error."

---

### AC-5.1 [MINOR] Story 5 — "First scrape funciona sin tocar código" — prd.txt:207
```
- [ ] First scrape funciona sin tocar código.
```
Untestable as written — no criterion for what constitutes "funciona" or "sin tocar código". If the dev must edit `.env` or `docker-compose.override.yml`, does that count as "tocar código"?

---

## 2. Goals & Metrics — Instrumentation Gaps

### Gap 2.1 [BLOCKER] Goal 1 — Measurement depends on Langfuse which is P1, not P0 — prd.txt:65 vs. prd.txt:299
```
Goal 1 Measurement: contador en SQLite + dashboard Langfuse.
REQ-013: Langfuse self-hosted opcional (P1, Should Have).
```
**Problem:** The primary success metric for the most important goal (95% success rate) depends on Langfuse, which is a P1 requirement that is off by default. If a team builds Phase 1–2 without Langfuse, Goal 1 is unmeasurable. The SQLite counter is mentioned but no schema, query, or reporting tool for it is defined.

**Fix needed:** Either (a) promote the SQLite counter + minimal CLI report to a P0 requirement, or (b) clarify that Goal 1 measurement is the SQLite counter alone and Langfuse is an enhanced view. The counter query and schema should be defined.

---

### Gap 2.2 [MAJOR] Goal 2 — "log de Judge + outcome de Remapper en Langfuse" — prd.txt:73
Same problem as Gap 2.1. Self-healing measurement requires Langfuse or a structured log query. Without defining the log schema (which fields signal "auto-remap succeeded" vs. "HITL required"), this metric is not computable from logs alone.

---

### Gap 2.3 [MAJOR] Goal 3 — LiteLLM cost tracking is not in any functional requirement — prd.txt:81
```
Goal 3 Measurement: cost guardrails enforcement + LiteLLM cost tracking.
```
LiteLLM is referenced in the executive summary stack context but there is no REQ for its integration, configuration, or the mechanism by which per-job cost is attributed and queryable. REQ-011 (cost guardrails) enforces a cap at $0.50/job but does not specify how cost is measured and surfaced for Goal 3's metric.

---

### Gap 2.4 [MAJOR] Goal 5 — "canary CI test en cada PR" — prd.txt:94
The canary test is referenced in REQ-019 (prd.txt:325) as P1 (Nice to Have) with "Issue #3" still open. A security goal with 0-tolerance should not depend on a Nice-to-Have unresolved issue. **This is a P0 security requirement mislabeled as P1.**

---

### Gap 2.5 [MINOR] SLI/SLO defined — prd.txt NFRs Observability section
```
SLI/SLO definidos: success rate, latencia, costo medio, tasa de remap.
```
This is aspirational. No SLO thresholds, measurement windows, or alerting definitions are provided. This is acceptable for v1 but should be flagged as a known gap requiring a follow-up document before Phase 5 (Hardening).

---

## 3. User Stories — Missing Edge Cases

### Missing 3.1 [BLOCKER] OTP timeout — no story or AC for the "user never responds" path
The OTP hard cap is 4 minutes (prd.txt:340). Story 1 AC defines the happy path (`POST /jobs/{id}/otp-confirmed` within 4 min — prd.txt:111) but there is no AC for: what happens when the 4-minute window expires without OTP confirmation? Does the job transition to `job.failed`? Is a `job.otp_timeout` event emitted? Is the browser session cleaned up? The circuit breaker (2 fail logins → 1h cooldown — prd.txt:355) presumably applies, but there is no explicit linkage.

---

### Missing 3.2 [BLOCKER] Account locked / wrong credentials — no story
The risks section mentions "banco bloquea cuenta del usuario por intentos fallidos" (prd.txt:~490) and the mitigation is the circuit breaker. However no User Story or AC covers: what is the user experience when credentials are wrong? What error is returned? Is `job.failed` emitted with a specific reason code? Does the circuit breaker trip on the first bad-credential attempt or only after 2?

---

### Missing 3.3 [MAJOR] Network loss mid-scrape — no story
Temporal provides durable execution, but there is no AC specifying the recovery behavior visible to the API consumer. After a worker restart mid-scrape: does the job resume from the last completed activity? Does the OTP window reset? Does `job.progress` re-emit? This is critical for integration developers.

---

### Missing 3.4 [MAJOR] Mapper fails to produce a valid map.json
Story 1 AC handles the case where `map.json` does not exist (prd.txt:109) but not the case where the Mapper agent runs and fails (network error, LLM timeout, schema validation failure). What is the job state? How does the operator recover?

---

### Missing 3.5 [MINOR] Concurrent scrape same bank+credential
REQ reliability states "max 1 scrape concurrente por (bank, credential)" (prd.txt NFRs). No User Story covers the rejection path — what does the API return when a second `POST /scrape` arrives for the same (bank, credential)?

---

### Missing 3.6 [MINOR] Remap proposal TTL expiration
Story 3 defines a 24h TTL for proposals (prd.txt:166) but no AC for what happens when TTL expires: is the proposal auto-rejected? Is a `job.remap_expired` webhook emitted? Is the original broken bank marked as `degraded`?

---

## 4. Functional Requirements — Ambiguity, Overlap, Priority Issues

### REQ-4.1 [BLOCKER] REQ-001 AC is under-specified — prd.txt:224
```
AC: idempotency keys, rate limiting por banco, bearer token auth single-org, OpenAPI 3.1 spec auto-generada.
```
"Idempotency keys" — no definition of the idempotency scope (which endpoints? what is the key header name? what is the window?). "Rate limiting por banco" — no numbers. These are testable only if the values are defined. REQ-001 references `docs/02-components/api.md` but the PRD must stand alone as a contract.

---

### REQ-4.2 [MAJOR] REQ-002 AC is untestable as written — prd.txt:230
```
AC: durabilidad cross-restart, time-skipping testing, replay determinístico.
```
"Durabilidad cross-restart" — pass/fail test requires: which activities survive? after how many restarts? "Replay determinístico" — this is a Temporal guarantee, not an AC for this system. These read as design properties, not acceptance criteria.

---

### REQ-4.3 [MAJOR] REQ-003 and REQ-006 overlap significantly — prd.txt:234 and prd.txt:251
REQ-003 (Mapper) and REQ-006 (Remapper) both use `browser-use + Claude vision` and both "emit a map.json patch/declarative". The distinction (full exploration vs. zone-targeted re-exploration) is stated in the description but the ACs do not differentiate them. REQ-006 AC just says "auto-apply o emit webhook según Judge" — the same behavior is implied by Story 3 ACs. This creates ambiguity about whether a failed initial Mapper should trigger the Remapper path.

---

### REQ-4.4 [MAJOR] REQ-012 duplicates Goal 1 metric — prd.txt:294
```
REQ-012 AC: 95% success rate sobre 100 scrapes reales.
```
This is identical to Goal 1. However REQ-012 is P0 while REQ-013 (Langfuse, needed to measure it) is P1. This creates a logical impossibility: you cannot pass REQ-012's AC in CI without the measurement infrastructure, which is P1.

---

### REQ-4.5 [MAJOR] REQ-019 is P2 (Nice to Have) but anchors Goal 5 — prd.txt:325
```
REQ-019: browser-use sensitive_data fuzz test. Issue #3. (Priority: Nice to Have)
```
Goal 5 has "0 tolerance, 100% target". Assigning the test that verifies it as P2 is a priority inversion. If Issue #3 is unresolved and the fuzz test is not built, Goal 5 has no measurement mechanism. This should be P0.

---

### REQ-4.6 [MINOR] REQ-007 (Secret Vault) AC references `docs/04-security/sandbox.md` — potential mismatch
REQ-007 is the Secret Vault but its AC references the sandbox doc. REQ-009 (Sandbox) is a separate requirement. Verify this is not a copy-paste error.

---

### REQ-4.7 [MINOR] REQ-015 and REQ-016 have no ACs
Looking at the P1 group: REQ-015 (SBOM) and REQ-016 (audit log) appear without ACs in the indexed content. Requirements without ACs cannot be verified as done.

---

## 5. NFRs — Latency Realism

### NFR-5.1 [MAJOR] p50 < 90s for 3-account scrape is plausible; p99 < 300s may not be — prd.txt:339-341
```
Scrape job end-to-end (banco con 3 cuentas, range 30 días): p50 < 90s, p95 < 180s, p99 < 300s.
```
These figures assume human OTP response is excluded from the latency budget. The PRD does not state this explicitly. If OTP wait (up to 4 minutes = 240s) is included in the p99 budget of 300s, there is only 60 seconds of headroom for: browser launch, login navigation, 3 account navigations, 3 Excel downloads, 3 Excel parses, and webhook emissions. That is unrealistic.

**Fix needed:** State explicitly that the latency SLA excludes OTP wait time and begins after `job.otp_confirmed` is received.

---

### NFR-5.2 [MAJOR] No latency budget for Mapper (one-time) in the scrape flow
When a new bank is added, the Mapper runs before the scrape. The Mapper has a separate cap of 5 minutes (prd.txt:342). Story 1 does not define whether the p99 < 300s SLA applies to a "scrape with mapping" job. If it does, the SLA is impossible to meet. If it doesn't, this must be stated.

---

### NFR-5.3 [MINOR] No latency SLA for the OTP signal path itself
"Webhook `job.otp_required` dispara" — no latency specified for how quickly after the login page OTP prompt appears the webhook must be delivered to the client. For a human to respond within 4 minutes, the webhook delivery latency matters.

---

### NFR-5.4 [MINOR] "Langfuse traces para todos los LLM calls (cuando habilitado)" — prd.txt NFRs Observability
The qualifier "cuando habilitado" creates a two-tier observability model. For the NFR to be binding, it needs to state what is the minimum observable state when Langfuse is disabled. Currently, the only fallback is Temporal UI + HAR files, which cannot reconstruct LLM call sequences or token costs.

---

## 6. Out of Scope — Items That Should Be Reconsidered

### OOS-6.1 [MAJOR] "Otros bancos PA (BAC, Banistmo, Global Bank) son v1.x" — prd.txt:474
The PRD lists these as out of scope but REQ-003, REQ-004, REQ-006, and the entire hexagonal architecture are designed for multi-bank extensibility. The DSL, port abstractions, and `banks/{bank}/map.json` convention all presuppose multi-bank. This is not a problem — it is good design — but it means the architecture is being built and tested against exactly one bank. The risk: the multi-bank abstraction may have untested assumptions that only surface when bank #2 is added. A P1 spike with Banistmo (already analyzed in project memory) during Phase 5 would validate the abstraction at near-zero cost.

---

### OOS-6.2 [MINOR] "Stealth / anti-bot evasion sofisticado" out of scope but no detection threshold defined
The mitigation is "if the bank blocks, we escalate." But there is no definition of "blocked" that the system can detect programmatically. If Banco General silently starts returning empty Excel files or redirecting to a "maintenance" page instead of an explicit HTTP error, the Validator/Judge must recognize this as a breakage. The BreakageEvent contract (prd.txt:160) lists "selector null, timeout, schema mismatch, HTTP error" — but not "empty result set" or "unexpected redirect post-login". These are real anti-scraping patterns that sit between "works" and "obvious block".

---

### OOS-6.3 [MINOR] "Captcha resolving" deferred but no detection story
If Banco General adds a captcha between now and Phase 6, the system will fail silently (likely a timeout or selector null BreakageEvent). The PRD should state: captcha detection is in scope (emit `job.failed` with `reason: captcha_detected`) even if solving is out of scope.

---

## 7. Open Questions — Phase 1/2 Blockers

All 5 open issues map to GitHub Issues (#1–#5). The concern is that some are framed as "post-launch" or "nice to have" when they are actually Phase 1 prerequisites:

### OQ-7.1 [BLOCKER] Issue #1 — Filter middleware redact + canary CI
This must be implemented in Phase 1 (the CI pipeline is defined in Phase 1, prd.txt:~417). The canary test must run on every PR from day 1 — not as a post-launch hardening item. If it slips to Phase 5, all interim commits could have credential leaks that are discovered only at hardening time. Should be a Phase 1 task with a spike in Week 1.

---

### OQ-7.2 [BLOCKER] Issue #3 — browser-use sensitive_data fuzz
Same argument as OQ-7.1. This must be resolved before any LLM call runs against real credentials. The spike should be Week 1–2 (before Mapper integration in Phase 2). The browser-use library's `sensitive_data` API must be empirically validated before the entire security model depends on it.

---

### OQ-7.3 [MAJOR] Issue #4 — Cosign keyless decision
This affects REQ-017 (Community maps trust model, P1). If Sigstore downtime blocks new signed maps, community contributions stall. The decision on keyless vs. long-lived signing keys should be made before Phase 4 (when community maps pipeline is built), not left open.

---

### OQ-7.4 [MINOR] Issue #2 — docker-socket-proxy hardening
This is a Phase 1 infrastructure decision. The sandbox spawning model (REQ-009) depends on how Docker socket access is scoped. If this changes post-implementation, REQ-009 may require rework. Should be resolved in Week 1 of Phase 1.

---

### OQ-7.5 [MINOR] Issue #5 — Webhook clock skew /time endpoint
REQ-020 is P2 (Nice to Have). This is a correct priority — the feature is a diagnostic aid, not a correctness requirement. The anti-replay window (±5 min, prd.txt:276) is what matters for security; the /time endpoint is optional tooling.

---

## 8. Internal Inconsistencies

### INC-8.1 [BLOCKER] PRD Date vs. Measurement Timeframes
- PRD date: 2026-05-09 (prd.txt:4)
- Goal 1 timeframe: "60 días post-lanzamiento de v1" (prd.txt:64)
- Goal 4 timeframe: "validación pre-lanzamiento" (prd.txt:88)
- Phase 6 ends at week 14 = approximately 2026-08-09 if starting immediately

No contradiction — but Goal 1's "100 corridas reales en 30 días" window nested inside "60 días post-lanzamiento" means the measurement window starts well after development ends. The PRD should clarify when "lanzamiento" is defined as occurring (after Phase 6 tag? after 3 betatesters sign off?).

---

### INC-8.2 [MAJOR] Goal 2 target (≥ 70% auto-remap) vs. Story 3 AC (confidence ≥ 0.85) — prd.txt:71 vs. prd.txt:162
Goal 2 measures operational success rate of auto-remap over time. Story 3 AC measures a single judge decision threshold. These are different things. A judge with confidence = 0.90 could still result in a failed remap (Remapper produces invalid patch). The Goal 2 metric requires tracking end-to-end auto-remap outcomes, not just judge decisions. There is no instrumentation defined for this outcome tracking beyond "log de Judge + outcome de Remapper en Langfuse" — which is only available when Langfuse is enabled (P1).

---

### INC-8.3 [MAJOR] REQ-011 cost guardrail ($0.50/job) vs. Goal 3 target ($0.10/scrape) — prd.txt:282 vs. prd.txt:79
The guardrail is the ceiling; the goal is the target. These do not contradict, but a job that hits the $0.50 guardrail and terminates with `budget_exceeded` still counts as a failed job for Goal 1 (success rate). The interaction between cost guardrails and success rate metrics is not defined. Can a job fail for budget reasons and still count as a "breakage" that triggers the Remapper? Or does it count as a plain failure?

---

### INC-8.4 [MINOR] Story 1 dependencies say "REQ-001..REQ-008" but REQ-009 (Sandbox) is required for any LLM browser activity — prd.txt:126
The browser-use Mapper (REQ-003) runs inside the sandbox container (REQ-009). Story 1's dependency list omits REQ-009. If REQ-009 is not implemented, Story 1 cannot complete even with REQ-001..REQ-008.

---

### INC-8.5 [MINOR] Phase 1 includes "canary redact test" but Issue #1 (Filter middleware redact) is an open GitHub issue
Phase 1 CI tasks include the canary test (prd.txt:~422) but the filter middleware it tests is listed as an unresolved GitHub Issue #1. Either the Phase 1 item presupposes Issue #1 is resolved as part of Phase 1 (which should be made explicit), or there is a sequencing gap.

---

### INC-8.6 [MINOR] "GPT-5" referenced in Why Solve This Now — prd.txt:50
```
LLMs con vision (Claude Sonnet 4.6, GPT-5) ya son capaces de mapear flujos web complejos
```
GPT-5 is mentioned as a capability reference but is not in the tech stack, not in any REQ, and not a supported LLM provider in the system. This is a documentation artifact that should be removed or replaced with a model that is actually in scope (Claude Sonnet 4.6, DeepSeek V3).

---

## Prioritized Issue List

| ID | Severity | Section | Issue |
|----|----------|---------|-------|
| AC-3.1 | BLOCKER | Story 3 AC | Judge output schema not defined; confidence threshold not testable |
| AC-1.1 | BLOCKER | Story 1 AC | Mapper auto-run AC missing observable outcomes |
| Gap-2.1 | BLOCKER | Goal 1 | Measurement infrastructure (Langfuse) is P1; counter schema not defined |
| OQ-7.1 | BLOCKER | Open Q | Issue #1 (credential redact) must be Phase 1 Week 1, not post-launch |
| OQ-7.2 | BLOCKER | Open Q | Issue #3 (sensitive_data fuzz) must be pre-Mapper integration spike |
| Missing-3.1 | BLOCKER | User Stories | OTP timeout path has no AC or state transition defined |
| Missing-3.2 | BLOCKER | User Stories | Wrong credentials / account locked path has no story |
| REQ-4.5 | MAJOR | REQ-019 | Security Goal 5 measurement test is P2 — should be P0 |
| Gap-2.4 | MAJOR | Goal 5 | canary CI test labeled P1/P2 while Goal 5 is P0 zero-tolerance |
| NFR-5.1 | MAJOR | NFRs | p99 < 300s is impossible if OTP wait is included; must be excluded explicitly |
| NFR-5.2 | MAJOR | NFRs | No SLA defined for scrape-with-mapping (Mapper adds up to 5 min) |
| INC-8.2 | MAJOR | Inconsistency | Goal 2 metric ≠ Story 3 AC; end-to-end remap success not instrumented |
| INC-8.3 | MAJOR | Inconsistency | Cost guardrail failure impact on success rate not defined |
| AC-1.2 | MAJOR | Story 1 AC | Canonical schema not referenced in AC; test has no oracle |
| AC-2.1 | MAJOR | Story 2 AC | "Solo registros nuevos" contradicts 3-day buffer overlap |
| Missing-3.3 | MAJOR | User Stories | Network loss mid-scrape recovery behavior not defined for API consumer |
| REQ-4.1 | MAJOR | REQ-001 | Idempotency key spec (header, scope, window) not in PRD |
| REQ-4.3 | MAJOR | REQ-003/006 | Mapper vs. Remapper boundary ambiguous for failed initial mapping |
| INC-8.4 | MINOR | Story 1 | REQ-009 (Sandbox) missing from Story 1 dependencies |
| OOS-6.2 | MINOR | Out of Scope | Empty-result anti-bot pattern not in BreakageEvent contract |
| OOS-6.3 | MINOR | Out of Scope | Captcha detection (not solving) should be in scope |
| INC-8.6 | MINOR | Exec Summary | GPT-5 referenced but not in tech stack |

---

## Recommendations Before Development Starts

1. **Two mandatory spikes, Phase 1 Week 1:** Issue #1 (credential redact filter) and Issue #3 (browser-use sensitive_data fuzz). Both are security foundations that cannot be validated retroactively. Convert to Phase 1 tasks.

2. **Define three missing contracts:** (a) Judge output schema (fields, types, valid enums), (b) canonical transaction schema (or authoritative reference to parser.md), (c) SQLite success counter schema + CLI query for Goal 1 measurement without Langfuse.

3. **Amend three NFR statements:** (a) exclude OTP wait from p99 latency SLA, (b) define separate latency budget for "first scrape with auto-mapping", (c) clarify minimum observability guarantees when Langfuse is disabled.

4. **Add three missing ACs:** (a) OTP timeout state transition, (b) wrong credentials / circuit breaker trigger, (c) concurrent `POST /scrape` for same (bank, credential) rejection response.

5. **Fix priority inversion:** REQ-019 (sensitive_data fuzz test) from P2 to P0. It directly measures Goal 5, which has zero-tolerance policy.
