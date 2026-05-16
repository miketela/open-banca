# HU06 — USER-TEST 3: Banco General piloto (task 18)

> Issue: TBD
> Branch: TBD
> Estado: draft
> Depende de: HU03 (smoke E2E real BG completado), taskmaster:16, taskmaster:17

## Contexto

Tercer checkpoint. Valida que Banco General funciona como banco productivo: mapping completo, parser robusto contra Excels reales, schema canónico bien formado, dedup funcionando. Esta HU es esencialmente la confirmación humana de que HU03 (smoke E2E) cumple los criterios del PRD para "banco piloto productivo".

## Acceptance Criteria

- [ ] HU03 cerrado con éxito (smoke E2E real BG verde).
- [ ] `map.json` BG firmado con cosign keyless (o documentado por qué se posterga).
- [ ] `parser.json` BG cubre los 3 tipos de cuenta del scope v1: savings, checking, credit_card.
- [ ] Resultado canónico de HU03 inspeccionado manualmente: balances + transacciones coinciden con lo que el operador ve en la web del banco.
- [ ] Dedup 3-niveles validado: ejecutar el mismo scrape 2 veces seguidas (incremental) NO duplica transacciones.
- [ ] Cursor incremental se persiste y reanuda correctamente desde donde quedó.
- [ ] Documentado en `docs/06-banks/banco-general.md` el estado real (cobertura, edge cases conocidos).

## Plan técnico

1. Confirmar HU03 done.
2. Comparar resultado canónico vs UI del banco:
   ```bash
   curl -s http://localhost:8000/jobs/<JOB_ID>/result \
     -H "Authorization: Bearer $OPEN_BANCA_API_KEY" | jq '.accounts[].balance, .transactions | length'
   ```
   Manual: matchear con dashboard BG.
3. Ejecutar scrape incremental 2x seguidos y validar dedup:
   ```bash
   curl -X POST http://localhost:8000/scrape -d '{"bank_id":"banco_general","mode":"incremental"}' ...
   # esperar completion
   curl -X POST http://localhost:8000/scrape -d '{"bank_id":"banco_general","mode":"incremental"}' ...
   # comparar transaction counts (segundo run debe ser 0 transacciones nuevas o muy pocas)
   ```
4. Documentar findings en `docs/06-banks/banco-general.md`.

## Tests

- Tests del parser DSL contra fixtures Excel reales (`packages/banks/banco_general/tests/test_parser.py`).
- Dedup tests (`packages/adapters/storage/tests/test_dedup_engine.py`).
- Validation manual del resultado vs UI.

## Riesgos

- **Edge cases no cubiertos**: tarjetas de crédito tienen schema distinto (`credit_limit`, `available_credit`, etc.). Verificar que el parser los maneja.
- **Cosign signing setup**: si nunca se firmó un map, requiere setup GitHub OIDC en CI. Puede postergarse documentando la razón.

## Definition of Done

- AC todos checked.
- `task-master set-status --id=18 --status=done`.
- Doc del banco actualizado con estado real.
