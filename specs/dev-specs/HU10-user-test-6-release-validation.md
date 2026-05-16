# HU10 — USER-TEST 6: v1.0.0 release validation (task 35)

> Issue: #16
> Branch: TBD
> Estado: draft
> Depende de: HU09

## Contexto

Sexto y último checkpoint. Validación humana final de que el release v1.0.0 cumple las métricas del PRD §Goals/Métricas:
- 1 banco productivo (BG) con ≥95% éxito de scrape en una semana.
- 3 betatesters completaron scrape sin asistencia.
- 100 scrapes consecutivos sin leak de credenciales.
- Cost real ≤ $0.50/job en p95.

## Acceptance Criteria

- [ ] Métricas del stress test (HU09) revisadas y cumplen umbrales del PRD.
- [ ] Reportes de betatesters revisados; cero bloqueadores críticos abiertos.
- [ ] Una semana de corridas reales en producción local del operador: ≥95% success rate (medido manualmente o vía OTel).
- [ ] Canary redact verde durante toda la semana de corridas.
- [ ] Documentación final revisada: cualquier visitante puede ir de `git clone` a primer scrape exitoso siguiendo solo el README.
- [ ] License AGPL-3.0 visible y correcta en repo + release.
- [ ] Issues abiertas trackeadas: ninguna marcada `release-v1` queda en estado pending al cierre.

## Plan técnico

Validación humana, no código nuevo:

1. Revisar `docs/05-operations/v1-stress-report.md`.
2. Revisar feedback de los 3 betatesters (issues con label `release-v1`).
3. Correr scrape manual diariamente por 7 días, llevar log de éxitos/fallas.
4. Verificar canary diariamente: `uv run pytest -m canary`.
5. Code review del repo state vs metas del PRD.

## Tests

Manual checklist.

## Riesgos

- **Métricas no cumplen**: rollback de tag v1.0.0, abrir v1.0.1 con fixes.
- **Bug crítico descubierto en la semana**: hotfix branch + v1.0.1 patch release.

## Definition of Done

- AC todos checked.
- `task-master set-status --id=35 --status=done`.
- Anuncio del release publicado (blog/twitter/donde corresponda).
- Roadmap v2 esbozado (próximo banco, features postergados).
