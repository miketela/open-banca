# HU validation checklist (operador)

Checklist paso a paso para cerrar HU01–HU03 sin depender de CI. Complementa [`specs/dev-specs/`](../specs/dev-specs/) y [`deployment.md`](deployment.md).

## Prerrequisitos globales

- [ ] `uv sync --all-packages` y `uv run pytest -m "not live and not canary" -q` verdes
- [ ] `.env` copiado de `.env.example` con secrets reales (no commitear)
- [ ] Swap desactivado y checklist M-1–M-7 de [`deployment.md`](deployment.md) completado
- [ ] `ANTHROPIC_API_KEY` válida; cuota suficiente para mapper/smoke

---

## HU01 — Mapper en vivo + HAR

**Spec:** [`specs/dev-specs/HU01-mapper-en-vivo-bg-har.md`](../../specs/dev-specs/HU01-mapper-en-vivo-bg-har.md)

| # | Paso | Comando / acción | OK |
|---|------|------------------|-----|
| 1 | Suite offline verde | `uv run pytest -m "not live" -q` | ☐ |
| 2 | Credenciales en vault | `uv run open-banca register-credentials --bank banco_general` | ☐ |
| 3 | Mapper en vivo + HAR | `OPEN_BANCA_LIVE_MAPPER=1 uv run open-banca run-mapper --bank banco_general --capture-har` | ☐ |
| 4 | HAR redactado guardado | `packages/banks/banco_general/fixtures/har/` | ☐ |
| 5 | Schema map | `uv run pytest packages/banks/banco_general/tests/test_map_schema.py -q` | ☐ |
| 6 | Canary sin leaks | `uv run pytest -m canary -q` | ☐ |

**Estado operador:** draft → in-progress cuando paso 1 verde; done cuando 3–6 completos.

---

## HU02 — Deploy docker-compose

**Spec:** [`specs/dev-specs/HU02-deploy-local-compose.md`](../../specs/dev-specs/HU02-deploy-local-compose.md)

| # | Paso | Comando / acción | OK |
|---|------|------------------|-----|
| 1 | Build imágenes | `docker compose build api sandbox-runner` | ☐ |
| 2 | Levantar stack | `docker compose up -d` | ☐ |
| 3 | UI Temporal (opcional) | `docker compose --profile ui up -d` | ☐ |
| 4 | Validación automatizada | `bash scripts/validate_hu02_deploy.sh` | ☐ |
| 5 | Smoke compose (arranca/apaga) | `bash scripts/smoke_compose.sh` | ☐ |
| 6 | Worker conectado | `docker compose logs temporal-worker --tail 30 \| grep -i "worker started"` | ☐ |

**Servicios base (5):** `docker-socket-proxy`, `postgres-temporal`, `temporal-server`, `api`, `temporal-worker`.

**Endpoints:** `GET http://localhost:8080/healthz` (200), `GET /readyz` (200 cuando worker listo).

**Estado operador:** in-progress tras paso 2; done cuando paso 4 exit 0 y AC del spec marcados.

---

## HU03 — Smoke E2E Banco General

**Spec:** [`specs/dev-specs/HU03-smoke-e2e-banco-general.md`](../../specs/dev-specs/HU03-smoke-e2e-banco-general.md)

| # | Paso | Comando / acción | OK |
|---|------|------------------|-----|
| 1 | HU02 verde | `bash scripts/validate_hu02_deploy.sh` | ☐ |
| 2 | Cookbook curl | `export API_KEY=... CREDENTIAL_REF=...; bash scripts/validate_hu03_smoke.sh` | ☐ |
| 3 | Device OTP listo | App Banco General / Clave Móvil | ☐ |
| 4 | POST /scrape 202 | Bearer + `Idempotency-Key` (ver script) | ☐ |
| 5 | Webhook OTP | `job.human_input_required` / OTP en webhook.site | ☐ |
| 6 | Confirmar OTP | `POST /jobs/{id}/otp-confirmed` | ☐ |
| 7 | job.completed | Webhook firmado HMAC ≤5 min | ☐ |
| 8 | Resultado N>0 | `GET /jobs/{id}/result` | ☐ |
| 9 | Cost < $0.50 | Logs / telemetry | ☐ |
| 10 | Incremental | Segundo `POST /scrape` mode=incremental | ☐ |
| 11 | Evidencia | Completar [`banco-general-e2e-evidence.md`](../06-banks/banco-general-e2e-evidence.md) | ☐ |
| 12 | Canary post-run | `uv run pytest -m canary -q` | ☐ |

**Estado operador:** pending hasta HU02 done; requiere intervención humana OTP (pasos 3–7).

---

## Scripts de referencia

| Script | Propósito |
|--------|-----------|
| [`scripts/validate_hu02_deploy.sh`](../../scripts/validate_hu02_deploy.sh) | Servicios + healthz/readyz |
| [`scripts/validate_hu03_smoke.sh`](../../scripts/validate_hu03_smoke.sh) | Curl cookbook scrape flow |
| [`scripts/smoke_compose.sh`](../../scripts/smoke_compose.sh) | Smoke CI local (build + up + down) |

## Siguiente HU

Tras HU03 done → HU06 piloto ([`HU06-user-test-3-bg-piloto.md`](../../specs/dev-specs/HU06-user-test-3-bg-piloto.md)).
