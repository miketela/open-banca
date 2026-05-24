# HU01 — Mapper en vivo a Banco General + HAR fixture

> Issue: #7
> Branch: TBD
> Estado: in-progress
> Depende de: plan-fase:F0, plan-fase:F1, plan-fase:F2 (suite verde antes de tocar live)
> **Notas ops (2026-05-24):** Código Fase 1 listo. Pendiente mapper en vivo + HAR. Checklist: [`docs/05-operations/hu-validation-checklist.md`](../../docs/05-operations/hu-validation-checklist.md) § HU01.

## Contexto

El `map.json` y `parser.json` actuales de Banco General fueron generados en una corrida temprana y no han sido revalidados contra el sitio real reciente. El folder `packages/banks/banco_general/fixtures/har/` está vacío (solo README + .gitignore), lo que significa que no hay replay determinista posible en CI.

Esta HU corre el Mapper en vivo contra Banco General con credenciales del operador, regenera/valida el `map.json`, y captura un HAR fixture redactado que sirve como base de tests deterministas a futuro.

## Acceptance Criteria

- [ ] `map.json` validado en vivo contra Banco General actual (login + nav a cuenta + descarga Excel funciona end-to-end).
- [ ] `map.json` pasa `test_map_schema.py` (`uv run pytest packages/banks/banco_general/tests/test_map_schema.py`).
- [ ] HAR fixture commiteado en `packages/banks/banco_general/fixtures/har/` con al menos: `login.har`, `nav_account.har`, `download_excel.har`.
- [ ] HAR redactado: 0 hits del canary redact test (`uv run pytest -m canary`).
- [ ] Tests de dry-run scraper pasan con el HAR nuevo.

## Plan técnico

1. **Suite verde primero** (no proceder si Fase 2 del plan no terminó):
   ```bash
   uv run pytest -m "not live" --tb=short
   ```
2. **Registrar credenciales operador** (CLI ya existente):
   ```bash
   uv run open-banca register-credentials --bank banco_general
   ```
3. **Correr Mapper en vivo**:
   ```bash
   export OPEN_BANCA_LIVE_MAPPER=1
   uv run open-banca run-mapper --bank banco_general --capture-har
   ```
   - Si el comando no soporta `--capture-har`, agregarlo (en `packages/cli/src/open_banca_cli/commands/run_mapper.py`) usando Playwright `context.tracing.start(har_recording=...)`.
4. **Redactar HAR** antes de commit:
   - Pass por `scripts/redact_har.py` (crear si no existe) que strip query params sensibles, headers `Authorization`, `Cookie`, `Set-Cookie`, request bodies de login.
5. **Validar schema**:
   ```bash
   uv run pytest packages/banks/banco_general/tests/test_map_schema.py
   uv run pytest packages/banks/banco_general/tests/test_dry_run_scraper.py
   uv run pytest -m canary
   ```
6. **Commit** con mensaje `feat(banco-general): real-site map.json validation + HAR fixture`.

## Tests

- `test_map_schema.py` pasa (todas las reglas de schema del map).
- `test_dry_run_scraper.py` corre el runner contra el HAR replayed y produce transacciones válidas.
- `test_har_redact_canary` (parte de `-m canary`) — busca strings sensibles en el HAR.

## Riesgos

- **Cuenta BG con OTP demora**: requerirá confirmar push en device físico durante el run del Mapper.
- **Sitio BG cambió desde el último mapping**: el Mapper puede producir un `map.json` distinto; eso es exactamente el objetivo de esta HU. Si cambió mucho, valida que el `parser.json` sigue siendo compatible con el Excel descargado.
- **Cost guardrail Claude Sonnet vision**: una corrida de mapping puede costar $0.20-$0.40. Verificar que el cap global $0.50/job no se viola.

## Definition of Done

- AC todos checked.
- HAR fixture en git, redactado.
- `task-master set-status --id=16 --status=done` (revalidación del task 16 ya done, pero confirma con map real).
- Plan F3 marked complete en `launch-api-bg-scrape_695d474e.plan.md` todos.
