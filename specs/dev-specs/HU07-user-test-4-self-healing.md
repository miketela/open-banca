# HU07 — USER-TEST 4: Self-healing flow (task 22)

> Issue: #13
> Branch: TBD
> Estado: draft
> Depende de: taskmaster:19, taskmaster:20, taskmaster:21

## Contexto

Cuarto checkpoint. Valida el ciclo completo de self-healing: detección de breakage → Judge decide → Remapper produce patch → HITL approval → re-corrida exitosa. v1 ADR-0013-amendment dice que TODO va a HITL (no auto-apply), así que esta HU prueba el flujo HITL.

## Acceptance Criteria

- [ ] Simulación de breakage: modificar el `map.json` BG para romper deliberadamente un selector.
- [ ] Correr scrape → BreakageEvent se emite.
- [ ] Judge se invoca y produce `route: human_required` (v1 hardcoded).
- [ ] Webhook `job.remap_proposed` llega firmado.
- [ ] Endpoint `POST /maps/banco_general/proposals/{id}/approve` funciona y resume el workflow.
- [ ] Remapper produce un nuevo `map.json` (commit via git apply en branch separada o como diff).
- [ ] Re-corrida con el nuevo map funciona end-to-end.
- [ ] Cost guardrail del Judge respetado: NO se invoca más de 1x por breakage hash.

## Plan técnico

1. Crear branch sacrificial: `task-22-self-healing-test`.
2. Romper map manualmente:
   ```bash
   # editar packages/banks/banco_general/map.json y cambiar un selector
   jq '.steps[0].selector = "#broken-selector-xyz"' packages/banks/banco_general/map.json > /tmp/broken.json
   mv /tmp/broken.json packages/banks/banco_general/map.json
   ```
3. Correr scrape y esperar webhook `remap_proposed`:
   ```bash
   curl -X POST http://localhost:8000/scrape ...
   # observar webhook.site
   ```
4. Obtener proposal ID de la issue/webhook payload.
5. Aprobar:
   ```bash
   curl -X POST http://localhost:8000/maps/banco_general/proposals/$PROPOSAL_ID/approve ...
   ```
6. Verificar que Remapper produjo el diff. Inspeccionar el nuevo `map.json`.
7. Re-correr scrape, validar éxito.
8. Restaurar map original (revertir branch).

## Tests

- Existentes en `packages/adapters/orchestrator/tests/test_judge_workflow.py` deben pasar.
- Smoke manual descrito arriba.

## Riesgos

- **Cost del Remapper alto**: una invocación puede gastar $0.30+. Verificar guardrails.
- **HITL UX cumbersome**: aprobar via curl es feo pero v1 no tiene UI. Documentar limitación.
- **Map roto pushed accidental**: trabajar en branch sacrificial y NO mergear el map roto a main/develop.

## Definition of Done

- AC todos checked.
- `task-master set-status --id=22 --status=done`.
- Branch sacrificial eliminada post-test.
- Map BG restaurado al estado validado en HU01.
