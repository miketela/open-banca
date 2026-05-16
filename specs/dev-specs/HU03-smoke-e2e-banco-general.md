# HU03 — Smoke E2E real Banco General

> Issue: #9
> Branch: TBD
> Estado: draft
> Depende de: HU01, HU02

## Contexto

Primer scrape end-to-end real contra Banco General usando el stack docker-compose desplegado en HU02 y el `map.json` validado en HU01. Es el momento de la verdad: valida que toda la cadena (API → Temporal → sandbox → browser → BG → Excel → parser → validator → webhook → storage → result endpoint) funciona en producción local.

## Acceptance Criteria

- [ ] `POST /scrape` retorna 202 con `job_id` válido.
- [ ] Webhook `job.otp_required` llega firmado HMAC-SHA256 a webhook.site dentro de 30s.
- [ ] OTP confirmado via `POST /jobs/{id}/otp-confirmed` desbloquea el workflow.
- [ ] Webhook `job.completed` llega firmado dentro de los 5 min siguientes al OTP.
- [ ] `GET /jobs/{id}/result` retorna schema canónico con `N > 0` transactions y al menos 1 account.
- [ ] Cost total reportado (en logs o telemetry) **< $0.50**.
- [ ] Canary redact test pasa post-corrida: cero credenciales en logs, traces, HAR, ni DB.
- [ ] Cursor incremental persistido: una segunda corrida con `mode=incremental` arranca desde el cursor de la primera.

## Plan técnico

1. **Verificar prerequisitos**:
   ```bash
   curl -fsS http://localhost:8000/health
   docker compose ps
   ```

2. **Confirmar credenciales en vault**:
   ```bash
   docker compose exec temporal-worker open-banca list-credentials
   ```

3. **Iniciar scrape full_historical**:
   ```bash
   JOB_ID=$(curl -X POST http://localhost:8000/scrape \
     -H "Authorization: Bearer $OPEN_BANCA_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"bank_id":"banco_general","mode":"full_historical"}' \
     | jq -r .job_id)
   echo "Job: $JOB_ID"
   ```

4. **Poll status y esperar OTP webhook** (revisar webhook.site dashboard):
   ```bash
   watch -n 5 "curl -s http://localhost:8000/jobs/$JOB_ID -H 'Authorization: Bearer $OPEN_BANCA_API_KEY' | jq .status"
   ```

5. **Confirmar push en device físico del operador (app Banco General)**, luego:
   ```bash
   curl -X POST http://localhost:8000/jobs/$JOB_ID/otp-confirmed \
     -H "Authorization: Bearer $OPEN_BANCA_API_KEY"
   ```

6. **Esperar webhook `job.completed`** (≤5 min). Si llega `job.failed`, capturar logs:
   ```bash
   docker compose logs temporal-worker --tail 200
   ```

7. **GET resultado**:
   ```bash
   curl -s http://localhost:8000/jobs/$JOB_ID/result \
     -H "Authorization: Bearer $OPEN_BANCA_API_KEY" | jq .
   ```

8. **Validar firma HMAC del webhook** recibido en webhook.site (manual o con script):
   ```bash
   echo -n "<payload>" | openssl dgst -sha256 -hmac "$OPEN_BANCA_WEBHOOK_SECRET"
   # comparar con header X-OpenBanca-Signature
   ```

9. **Canary redact post-corrida**:
   ```bash
   uv run pytest -m canary
   docker compose logs --no-color | grep -iE "(password|cedula|<your-bg-username>)" && echo "LEAK!" || echo "OK"
   ```

10. **Segunda corrida incremental** para validar cursor:
    ```bash
    curl -X POST http://localhost:8000/scrape \
      -H "Authorization: Bearer $OPEN_BANCA_API_KEY" \
      -d '{"bank_id":"banco_general","mode":"incremental"}'
    ```

## Tests

- Manuales (este HU es un smoke, no unit tests).
- AC checklist cubre toda la validación.

## Riesgos

- **OTP timeout 4 min**: device físico debe estar listo. Si no llega push en 4 min, workflow aborta con `otp_timeout`.
- **BG cambió UI mid-run**: BreakageEvent dispararía Judge → HITL. Si pasa, abrir HU07 (self-healing flow).
- **Cost guardrail dispara**: si Mapper no estaba cacheado (primer run en este stack), puede ir a $0.50; ver logs de cost_tracker.
- **Webhook receptor no firma bien**: usar webhook.site permite inspeccionar headers; si la firma no matchea, debug en `packages/adapters/api/webhooks/` y `packages/adapters/orchestrator/activities/emit_webhook.py`.

## Definition of Done

- AC todos checked.
- Resultado canónico guardado como referencia: `docs/06-banks/banco-general-e2e-evidence.md` (transacciones count + screenshots de webhook.site).
- Plan F5 marked complete.
- Si todo pasó, HU06 (USER-TEST 3 BG piloto) puede cerrarse.
