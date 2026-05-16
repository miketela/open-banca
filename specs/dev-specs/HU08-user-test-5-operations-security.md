# HU08 — USER-TEST 5: Operations + security (task 29)

> Issue: TBD
> Branch: TBD
> Estado: draft
> Depende de: taskmaster:23, taskmaster:24, taskmaster:25, taskmaster:26, taskmaster:27, taskmaster:28

## Contexto

Quinto checkpoint. Valida que las capas de operations + security funcionan en producción local:
- Sandbox Docker per-job aislado correctamente.
- docker-socket-proxy bloquea operaciones no permitidas.
- Webhooks firman + retry + DLQ.
- Cost guardrails enforcing limits.
- Observabilidad (Langfuse + OTel) capturando datos relevantes (si profile activo).
- Community maps trust model: maps sin firma rechazados.

## Acceptance Criteria

- [ ] Sandbox: un job spawnea container efímero con network allowlist; container destruido al terminar.
- [ ] docker-socket-proxy: intento de operación bloqueada (ej. `docker pull` desde el worker) falla con permission denied.
- [ ] Webhook delivery: simular receptor down → retry exponencial → si excede max attempts, evento va a DLQ.
- [ ] Cost guardrail: forzar un job que excede $0.50 → workflow aborta con `cost_cap_exceeded`.
- [ ] Cost guardrail: 3 remap attempts en 24h → 4to bloqueado por rate limit.
- [ ] Circuit breaker: 2 logins fallidos consecutivos → 1h cooldown.
- [ ] Langfuse (si profile activo) muestra trazas de la corrida HU03.
- [ ] OTel exporter (si profile activo) muestra spans correctos.
- [ ] Community maps: poner un map en `community/` sin firma → runtime rechaza con `unsigned_map`.

## Plan técnico

Tests individualizados:

```bash
# Sandbox
uv run pytest packages/adapters/sandbox/tests/test_isolation.py
# Verificar manualmente que el container no puede llegar a hosts no allowed:
docker compose exec temporal-worker docker exec <sandbox-id> curl -m 5 https://google.com  # debe fallar

# Webhooks retry + DLQ
uv run pytest packages/adapters/orchestrator/tests/test_webhook_retry_dlq.py

# Cost guardrails
uv run pytest packages/adapters/llm/tests/test_cost_abort.py
uv run pytest -k "rate_limit or circuit_breaker"

# Observabilidad
docker compose --profile langfuse up -d
# correr scrape, abrir Langfuse UI, validar traza
```

## Tests

Suite completa anterior + observación manual de Langfuse/OTel.

## Riesgos

- **Profile langfuse no testeado**: si nunca se levantó, puede tener errores de config.
- **Circuit breaker 1h en testing es lento**: usar fake clock o reset manual.

## Definition of Done

- AC todos checked.
- `task-master set-status --id=29 --status=done`.
- Evidencia: outputs de tests + screenshots de Langfuse (si aplica).
