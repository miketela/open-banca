# PRD Change Requests — ADR-0013 Amendment (P0-6)

**From:** agent-a1f3510635c24ef6e (architecture agent)
**To:** agent-prd
**Date:** 2026-05-10
**Branch:** worktree-agent-a1f3510635c24ef6e

---

## Change 1 — REQ-005: Auto-apply threshold → v1 HITL-only

**Current text in prd.txt REQ-005:**
> confidence ≥ 0.85 AND risk == low → auto-apply

**Replace with:**
> v1: siempre HITL; confidence/risk sólo telemetría. Auto-apply diferido a v1.x hasta tener baseline empírica calibrada (≥ 0.95 precision sobre ≥ 50 eventos reales).

**Rationale:** ADR-0013 amendment accepted 2026-05-10. Threshold 0.85 es arbitrario sin data histórica. Loop circular: Judge (texto) decide sobre evidencia OCR sin ver el screenshot real. Auto-apply en piloto puede mutar map.json incorrectamente y bloquear operador.

---

## Change 2 — Goal 2: Self-healing target redefinition

**Current text:**
> Self-healing target ≥ 70% auto-remap exitoso

**Replace with:**
> Self-healing target v1: ≥ 70% de breakages que requieren remap se resuelven con 1 sólo HITL approval (sin escalar a multi-touch ni cancelarse por timeout). Auto-remap autónomo diferido a v1.x.

**Rationale:** Goal 2 era impracticable en v1 sin baseline calibrada. La redefinición mantiene el espíritu (alta tasa de resolución rápida) sin presuponer que el sistema puede actuar autónomamente antes de tener evidencia de que su juicio es confiable.

---

## DONE Signal

DONE: worktree-agent-a1f3510635c24ef6e P0-6 HITL-only

Files committed in branch `worktree-agent-a1f3510635c24ef6e`:
- `docs/adr/0013-amendment-hitl-only-v1.md` — new ADR amendment (Nygard format)
- `docs/02-components/judge-agent.md` — auto-apply path marked deferred to v1.x
- `docs/03-flows/remap-detection.md` — v1 HITL note + diagram updated
- `docs/03-flows/remap-approval.md` — auto-apply diagram deferred, HITL table updated
