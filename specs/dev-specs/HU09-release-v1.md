# HU09 — v1.0.0 release: E2E + stress + docs + tag (task 30)

> Issue: #15
> Branch: TBD
> Estado: draft
> Depende de: HU04, HU05, HU06, HU07, HU08

## Contexto

Release task. Reúne los 5 USER-TESTs previos cerrados, valida con betatesters externos, ejecuta stress test, completa docs y genera el tag v1.0.0.

## Acceptance Criteria

- [ ] 3 betatesters externos completan un scrape end-to-end sin asistencia (instalación via docker-compose desde un release).
- [ ] Stress test: 100 scrapes consecutivos contra Banco General (puede mockearse parcialmente). Métricas:
  - p95 cost ≤ $0.50/job
  - p95 latencia ≤ 4 min (excluyendo OTP wait)
  - 0 leaks en canary
  - <5% failure rate
- [ ] Docs completas: `README.md` con quickstart, `docs/05-operations/deployment.md`, `docs/06-banks/banco-general.md`, troubleshooting guide.
- [ ] CHANGELOG.md con notas de v1.0.0.
- [ ] Tag `v1.0.0` creado en git + GitHub Release.
- [ ] Docker image publicada (si aplica) en GHCR o equivalente.

## Plan técnico

1. **Betatester onboarding**:
   - Identificar 3 betatesters (preferiblemente que ya tengan cuenta BG real).
   - Darles acceso al release pre-tag (branch `release/v1.0.0-rc1`).
   - Recolectar feedback en issues separadas.

2. **Stress test**:
   ```bash
   # script en scripts/stress_test.py (crear)
   uv run python scripts/stress_test.py --count 100 --bank banco_general
   ```
   - Salida: CSV con latencias y costos por job.
   - Generar reporte en `docs/05-operations/v1-stress-report.md`.

3. **Docs**:
   - Quickstart en README: `git clone → cp .env.example .env → editar → docker compose up`.
   - Troubleshooting: errores comunes (OTP timeout, cost exceeded, sandbox failure).

4. **CHANGELOG**:
   - Convención: Keep a Changelog format.
   - Incluir todas las decisiones técnicas relevantes de v1.

5. **Release**:
   ```bash
   git tag -a v1.0.0 -m "v1.0.0 - First production release: Banco General piloto"
   git push origin v1.0.0
   gh release create v1.0.0 --notes-file CHANGELOG.md --title "v1.0.0"
   ```

## Tests

- Stress test passing per AC.
- Betatester reports sin bloqueadores críticos.

## Riesgos

- **Betatesters no responden**: tener fallback plan (operadores conocidos del círculo cercano).
- **Stress test descubre bugs**: priorizar y fix antes de tag.
- **Anthropic rate limit en stress test**: pre-warmar quota, considerar mock parcial.

## Definition of Done

- AC todos checked.
- `task-master set-status --id=30 --status=done`.
- Tag v1.0.0 publicado.
- GitHub Release con notas completas.
