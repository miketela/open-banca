# HU02 — Deploy local docker-compose con `.env` real

> Issue: TBD
> Branch: TBD
> Estado: draft
> Depende de: HU01

## Contexto

`docker-compose.yml` define el stack producción-equivalente (postgres-temporal + temporal-server + temporal-worker + api + docker-socket-proxy), pero nunca se ha levantado con un `.env` real con keys productivas. Esta HU lo bootea localmente con todas las vars seteadas correctamente y valida que cada servicio queda healthy.

## Acceptance Criteria

- [ ] `.env` (no commiteado) tiene todas las vars requeridas con valores reales (no placeholders).
- [ ] `docker compose up -d` levanta los 5 servicios sin error.
- [ ] `docker compose ps` muestra todos como `healthy` o `running` (según healthcheck definido).
- [ ] `curl http://localhost:8000/health` retorna 200 con `status: ok`.
- [ ] Worker logs muestran "worker started" con las activities registradas (incluyendo las nuevas de plan F1).
- [ ] Temporal UI accesible en `http://localhost:8233` (si el profile está activo) y muestra el worker conectado.
- [ ] Documentado en `docs/05-operations/deployment.md` cómo replicar el deploy.

## Plan técnico

1. **Crear `.env`** copiando `.env.example` y llenando:
   - `OPEN_BANCA_MASTER_PASSPHRASE` → `openssl rand -base64 32`
   - `OPEN_BANCA_WEBHOOK_SECRET` → `openssl rand -base64 32`
   - `OPEN_BANCA_API_KEY` → `openssl rand -hex 32`
   - `TEMPORAL_DB_PASSWORD` → `openssl rand -base64 24`
   - `ANTHROPIC_API_KEY` → key real del operador
   - `OPEN_BANCA_MAPPER_MODEL=anthropic/claude-sonnet-4-6`
   - `OPEN_BANCA_REMAPPER_MODEL=anthropic/claude-sonnet-4-6`
   - `OPEN_BANCA_VALIDATOR_MODEL=anthropic/claude-haiku-4-5`
   - `OPEN_BANCA_JUDGE_MODEL=anthropic/claude-haiku-4-5`
   - Webhook receptor: URL de `webhook.site` para empezar.

2. **Levantar en orden** (dependencias):
   ```bash
   docker compose up -d postgres-temporal
   # esperar healthy: docker compose ps postgres-temporal
   docker compose up -d temporal-server
   docker compose up -d docker-socket-proxy
   docker compose up -d temporal-worker api
   ```

3. **Validar healthchecks**:
   ```bash
   docker compose ps
   curl -fsS http://localhost:8000/health
   docker compose logs temporal-worker --tail 30 | grep "worker started"
   ```

4. **Si algo falla**, capturar logs y diagnosticar antes de seguir:
   ```bash
   docker compose logs <service> --tail 100
   ```

5. **Actualizar `docs/05-operations/deployment.md`** con el procedimiento exacto.

## Tests

- Health endpoint responde 200.
- Worker se conecta a Temporal (logs lo confirman).
- Sandbox runner image construye (si está en `--profile sandbox`): `docker compose --profile sandbox build sandbox-runner`.

## Riesgos

- **Puertos ocupados**: 8000 (api), 7233 (temporal), 8233 (temporal-ui), 5432 (postgres). Si están en uso, fallar fast o documentar override.
- **Anthropic API key rate limit**: verificar quota suficiente antes de seguir a HU03.
- **docker-socket-proxy permissions**: el worker necesita poder spawnear containers via el proxy; si el ACL está mal, los activities `spawn_sandbox` fallarán en HU03.

## Definition of Done

- AC todos checked.
- Procedure documentado.
- `.env` real existe en disco (NO en git).
- Plan F4 marked complete.
