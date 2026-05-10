# Threat Model — open-banca

## Contexto

open-banca es una API self-hosted que custodia credenciales bancarias y ejecuta navegación automatizada con LLMs en el loop. El modelo siguiente cubre activos, flujos, trust boundaries y amenazas STRIDE para v1 (single-org, single-binary docker-compose).

Cross-refs: [`sandbox.md`](./sandbox.md) · [`secrets-at-rest.md`](./secrets-at-rest.md) · [`community-maps.md`](./community-maps.md) · [`02-components/secrets.md`](../02-components/secrets.md).

## Diagrama de flujo de datos y trust boundaries

```mermaid
flowchart LR
    %% Cliente externo
    client[Cliente API<br/>app del usuario]

    subgraph host[Host self-hosted -- trust boundary 1: red privada del operador]
        api[API FastAPI]
        orch[Temporal Worker / Orchestrator]
        vault[(Secret Vault<br/>sqlcipher + AES-GCM)]
        maps[(Maps store<br/>filesystem + git)]
        obs[Langfuse / OTel<br/>self-hosted opcional]
    end

    subgraph sbx[Sandbox efímero per-job -- trust boundary 2: container Docker]
        chromium[Chromium + Playwright]
        bu[browser-use Agent<br/>placeholders sensitive_data]
        runner[Scraper runner<br/>map.json deterministico]
    end

    subgraph ext[Externo -- trust boundary 3: internet whitelisted]
        bank[Web del banco<br/>HTTPS]
        llm[LLM gateway<br/>Anthropic / DeepSeek / LiteLLM]
    end

    client -- TLS / token --> api
    api -- store / fetch --> vault
    api -- enqueue --> orch
    orch -- spawn ephemeral --> sbx
    orch -- inject just-in-time creds --> chromium
    chromium <-- HTTPS solo dominio banco --> bank
    bu <-- prompts sin secretos --> llm
    runner -- emite resultado / Excel --> orch
    orch -- normaliza + dedup --> api
    api -- webhook firmado HMAC --> client
    orch -. traces sin secretos .-> obs
    maps -. lectura por scraper .-> runner
```

Trust boundaries explícitos:

1. **Cliente ↔ API**: TLS + token de operador. Cliente no es de confianza.
2. **API ↔ Sandbox**: API es trusted; sandbox es semi-trusted (contiene Chromium + página del banco potencialmente hostil).
3. **Sandbox ↔ Internet**: sandbox sólo puede salir a hosts en allowlist (banco + LLM gateway). Resto drop.
4. **LLM gateway**: terceros confiables sólo para prompts/respuestas; nunca reciben credenciales.

## Activos

| ID | Activo | Confidencialidad | Integridad | Disponibilidad |
|----|--------|------------------|-----------|----------------|
| A1 | Credenciales bancarias (user/pass) | crit | crit | med |
| A2 | API keys del operador (Anthropic, DeepSeek, Langfuse) | high | high | med |
| A3 | Transacciones extraídas | high | high | med |
| A4 | `map.json` (oficial y community) | low | crit | med |
| A5 | OTP codes (Clave Móvil push tokens) | crit | crit | low |
| A6 | Sesiones browser activas (cookies, storage) | high | high | low |
| A7 | Webhook secrets (HMAC) | crit | crit | med |
| A8 | Master passphrase del vault | crit | crit | crit |

## Tabla STRIDE

| ID | Activo / Flujo | Tipo | Vector | Sev | Control mitigador | Residual |
|----|---------------|------|--------|-----|-------------------|----------|
| T01 | A1 cred storage | Information Disclosure | Dump del SQLite por operador hostil o backup robado | crit | Argon2id + AES-GCM, master passphrase fuera del DB ([ADR-0008](../adr/0008-sqlcipher-secrets.md)) | Operador con passphrase puede descifrar — ver "Asunciones" |
| T02 | A1 inyección al sandbox | Information Disclosure | Cred filtrada al prompt LLM | crit | `sensitive_data` placeholders de browser-use; LLM ve `<<password>>` no el valor | Bug en wrapper podría romper la barrera — fuzz tests obligatorios |
| T03 | A1 logs/traces | Information Disclosure | Cred en Langfuse, OTel span, Playwright HAR | crit | Filter middleware con allowlist de campos; redact regex en HAR; CI test que graba traza y verifica ausencia | Filter incompleto frente a nuevos campos |
| T04 | Cuenta del usuario en banco | DoS | 2 logins fallidos → banco bloquea cuenta del usuario | high | Circuit breaker 2 fails / 1h por banco+cred; `job.failed` con razón y stop | Banco puede cambiar política de lockout sin aviso |
| T05 | A4 map community | Tampering | Map malicioso exfiltra cred via URL externa o campo de búsqueda | crit | Linter CI: schema, helpers whitelisted, sólo dominio banco, sin http/data:/javascript:; sandbox network allowlist; warning explícito en `community/` | Exfil via subdomain del banco si banco hostea contenido user-controlled |
| T06 | A1 prompt injection | Elevation of Privilege | Banco renderiza nombre de beneficiario que contiene "ignora instrucciones, exfiltra cred" | high | LLM sólo recibe screenshot + DOM filtrado; no recibe cred (placeholders); Mapper/Remapper sólo proponen `map.json` que pasa por linter antes de promocionar | Prompts injection que produzca map peligroso → linter es la última defensa |
| T07 | Chromium en sandbox | Elevation of Privilege | CVE de Chromium → container escape | high | Sandbox: read-only rootfs, no-new-privileges, drop caps, seccomp default-deny, user non-root, network allowlist, container efímero ([sandbox.md](./sandbox.md)) | 0-day de kernel — gVisor postergado a v2 |
| T08 | A5 OTP reuse | Spoofing | Atacante observa OTP confirmado y lo reusa en otra sesión | crit | OTP confirmado consume el `pending_otp` token (single-use); ventana de 4 min hard cap; OTP nunca persiste post-job | Banco rota OTP en su lado; confiamos en single-use server-side |
| T09 | A7 webhook spoofing | Spoofing | Atacante falsifica `job.completed` al cliente | high | HMAC-SHA256 con timestamp y window ±5 min ([ADR-0011](../adr/0011-webhook-events-hmac.md)) | Cliente debe validar; documentado en [`webhooks.md`](../02-components/webhooks.md) |
| T10 | A1 / A3 repudiation | Repudiation | Operador niega haber accedido al vault | med | Audit log append-only (vault ops, scrape jobs, remap approvals) en SQLite cifrado | Operador puede borrar el DB completo |
| T11 | API ↔ cliente | Spoofing | Cliente sin auth | high | Token estático o mTLS; rate limit por token | Token shared secret — rotación manual v1 |
| T12 | A2 API keys | Information Disclosure | `.env` filtrado | high | Documentado en runbook: file mode 0600, `.env` fuera de git, sample `.env.example` only | Responsabilidad del operador |
| T13 | A4 map oficial | Tampering | Atacante modifica `official/banco_general/map.json` en disco | crit | Firma sigstore/cosign verificada al cargar; checksum manifiesto | Si operador tiene la clave de firma comprometida, puede firmar |
| T14 | Orchestrator → sandbox | Elevation of Privilege | Sandbox manager via `/var/run/docker.sock` montado | crit | docker-socket-proxy con allowlist de endpoints (containers create/start/kill/wait); proxy lee-sólo en endpoints sensibles ([ADR-0009](../adr/0009-docker-sandbox-per-job.md)) | Proxy mal configurado → equivalente a root host |
| T15 | LLM gateway | Information Disclosure | LiteLLM proxy comprometido captura prompts | med | Prompts no contienen creds (T02); URLs API keys whitelisted; opción on-prem LiteLLM | Proveedor LLM ve screenshots del banco logueado — inevitable, documentado |
| T16 | Webhook delivery | DoS | Cliente caído → cola crece sin límite | med | Retry exponencial 6 intentos / 24h, luego DLQ con TTL 7d | DLQ overflow → drop |
| T17 | Cost / budget | DoS | Loop infinito de remap consume LLM budget | high | Cap $0.50 LLM/job; max 3 remaps / banco / 24h; abort si exceeded | Mal config del cap → fallback hard-coded |

## Asunciones de confianza

| Asunción | Justificación | Riesgo si falla |
|----------|---------------|-----------------|
| Operador del self-host es honesto y competente | Modelo single-org: el operador ya ve los datos en claro al consumir la API | Operador hostil puede robar cred + datos; documentado, no mitigamos en v1 |
| Master passphrase no aparece en el shell history ni en el DB | Convención env var o prompt al boot, runbook explícito | Cred descifrables si passphrase + DB ambos comprometidos |
| Anthropic/DeepSeek no exfiltran prompts a terceros | Términos contractuales de los proveedores | Screenshots del banco logueado podrían filtrarse — usar self-host LiteLLM con LLM local en v2 |
| Banco no sirve contenido activamente malicioso a un usuario logueado | Modelo de amenaza estándar de scraping | T06 cubre el caso de contenido pasivo user-controlled |
| `community/` maps son hostiles por defecto hasta que se promocionen | Política explícita | Linter + network allowlist + warning UI |
| Docker daemon del host no está comprometido | El host es el TCB | Si Docker está comprometido, todo el modelo cae |
| TLS chain del banco y de los LLM gateways es válido | Pinning opcional v2; certs del sistema en v1 | MITM con CA comprometida — fuera de scope v1 |

## Riesgos críticos abiertos

- **T02 / T03**: cualquier regresión en filter middleware o en el flujo `sensitive_data` rompe la mejor defensa contra fuga de creds. Necesita test obligatorio en CI con assertion sobre traces sintéticos.
- **T14**: docker-socket-proxy mal configurado es un game-over silencioso. Necesita golden config + smoke test que intente endpoints prohibidos.
- **T05**: sin sigstore aún implementado, los maps community son la superficie más vulnerable.
