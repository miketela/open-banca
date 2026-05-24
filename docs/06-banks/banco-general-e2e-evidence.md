# Banco General — evidencia E2E (HU03)

> Plantilla para documentar el primer smoke E2E real. **No commitear credenciales, OTP, ni PII.**

| Campo | Valor |
|-------|-------|
| Operador | |
| Fecha (UTC) | |
| Commit / tag | |
| Stack | docker-compose @ puerto |
| Spec | [HU03](../../specs/dev-specs/HU03-smoke-e2e-banco-general.md) |

## Prerrequisitos verificados

- [ ] HU01: `map.json` validado en vivo + HAR redactado
- [ ] HU02: `bash scripts/validate_hu02_deploy.sh` → exit 0
- [ ] `API_KEY` y `CREDENTIAL_REF` configurados (sin pegar valores aquí)

## Job 1 — full_historical

| Métrica | Valor |
|---------|-------|
| `job_id` | |
| `Idempotency-Key` | |
| POST /scrape HTTP | 202 |
| Tiempo hasta OTP webhook | s |
| Tiempo OTP confirm → completed | s |
| Cost total (USD) | |
| Transacciones (N) | |
| Cuentas (M) | |
| Webhook HMAC verificado | ☐ |

### Resultado canónico (resumen)

```json
{
  "job_id": "REDACTED",
  "transaction_count": 0,
  "account_count": 0,
  "sample_account_ids": ["REDACTED"]
}
```

### Webhooks recibidos

| Evento | Timestamp UTC | Firma HMAC OK |
|--------|---------------|---------------|
| `job.human_input_required` / OTP | | ☐ |
| `job.completed` | | ☐ |

Adjuntar capturas de webhook.site (sin headers con secretos).

## Job 2 — incremental

| Métrica | Valor |
|---------|-------|
| `job_id` | |
| Cursor previo (`GET /jobs/{id}/cursor`) | |
| Duplicados vs job 1 | 0 esperado |
| POST /scrape HTTP | 202 |
| completed OK | ☐ |

## Seguridad post-corrida

- [ ] `uv run pytest -m canary -q` verde
- [ ] `docker compose logs --no-color` sin password/cedula/username
- [ ] Artifacts / HAR sin PII

## Incidencias

| # | Descripción | Resolución |
|---|-------------|------------|
| | | |

## Conclusión

- [ ] **HU03 PASS** — N>0 transacciones, cost < $0.50, incremental OK, canary verde
- [ ] **HU03 FAIL** — motivo:

**Firma operador:** __________________ **Fecha:** __________
