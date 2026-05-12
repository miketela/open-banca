# Flow: Human Input Pause / Resume (preguntas de seguridad, captcha texto)

Flujo análogo al OTP pause/resume pero para **entrada textual** del operador mid-scrape. Disparado por step `prompt_user` (ADR-0021). Reutiliza el `BrowserSidecar` (ADR-0019) para mantener el browser context vivo durante el wait.

**Diferencia clave vs OTP**: el cliente HTTP **envía un valor** (`answer`) que el runner inyecta en el input del banco. OTP solo confirma "lista, sigue".

## Sequence diagram — happy path con cache miss

```mermaid
sequenceDiagram
    autonumber
    participant C as Cliente HTTP
    participant API as FastAPI
    participant W as ScrapeJobWorkflow
    participant HI as HumanInputAwaitActivity
    participant V as Secret Vault (security_q)
    participant Sidecar as BrowserSidecar
    participant Sand as Sandbox + Chromium
    participant Bank as Banco web

    W->>Sidecar: ejecuta step prompt_user
    Sidecar->>Sand: extract_text(question_selector)
    Sand-->>Sidecar: "¿Color favorito de su madre?"
    Sidecar-->>W: question_text + cache_key
    W->>V: lookup(cache_key)
    V-->>W: miss

    W->>HI: start(field_key, question_text, cache_key, timeout=240s)
    HI-->>W: heartbeat (every 15s)
    W-->>C: webhook job.human_input_required<br/>{field_key, question_text, expires_at, cached_attempted=false}

    Note over C: cliente recibe webhook + presenta al usuario / lookup operador
    C->>API: POST /jobs/{id}/human-input<br/>{field_key, answer, persist=true}
    API->>API: valida len ≤ 256 + whitelist printable
    API->>API: rate-limit (max 3 attempts por field_key)
    API->>W: signal human_input_provided{field_key, answer, persist}
    W->>HI: deliver signal (match por field_key)

    HI->>Sidecar: fill(selector, answer) + click(submit_selector?)
    Sidecar->>Sand: page.fill + page.click
    Sand->>Bank: submit security answer
    Bank-->>Sand: dashboard | next step
    Sidecar-->>HI: ok (next selector visible)

    HI->>V: if persist: store(cache_key, answer, ttl=90d)
    HI-->>W: ok
    W->>W: continúa al siguiente step

    Note over W: si el step siguiente devuelve BreakageEvent assertion_failed o http_error<br/>→ workflow invoca V.invalidate(cache_key) ANTES de propagar fallo
```

## Sequence diagram — cache hit (auto-fill)

```mermaid
sequenceDiagram
    autonumber
    participant W as ScrapeJobWorkflow
    participant HI as HumanInputAwaitActivity
    participant V as Secret Vault (security_q)
    participant Sidecar as BrowserSidecar
    participant Sand as Sandbox + Chromium
    participant Bank as Banco web

    W->>Sidecar: ejecuta step prompt_user
    Sidecar->>Sand: extract_text(question_selector)
    Sand-->>Sidecar: question_text
    Sidecar-->>W: question_text + cache_key
    W->>V: lookup(cache_key)
    V-->>W: hit (answer cifrado)

    W->>Sidecar: fill(selector, answer) + click(submit_selector?)
    Sidecar->>Sand: page.fill + page.click
    Sand->>Bank: submit
    Bank-->>Sand: next step ok

    Note over W: cache hit no dispara webhook ni signal<br/>no se invoca HumanInputAwaitActivity
    W->>W: continúa al siguiente step
```

## Sequence diagram — cache hit pero respuesta rechazada (invalidación)

```mermaid
sequenceDiagram
    autonumber
    participant W as ScrapeJobWorkflow
    participant V as Secret Vault
    participant Sidecar as BrowserSidecar
    participant Sand as Sandbox + Chromium
    participant Bank as Banco web
    participant API as FastAPI
    participant C as Cliente HTTP

    W->>V: cache hit, fill answer
    W->>Sidecar: fill + submit
    Sand->>Bank: submit
    Bank-->>Sand: "respuesta incorrecta, intente de nuevo" o redirect login
    Sidecar-->>W: BreakageEvent{cause=assertion_failed, step=post_prompt_user}

    Note over W: workflow detecta que step previo fue prompt_user con cache hit
    W->>V: invalidate(cache_key)
    V-->>W: ok (entrada borrada)

    W-->>C: webhook job.failed<br/>{reason: human_input_rejected, field_key}
    C->>API: el cliente puede reintentar con nuevo POST /scrape
```

## Rama de timeout

```mermaid
sequenceDiagram
    autonumber
    participant W as ScrapeJobWorkflow
    participant HI as HumanInputAwaitActivity
    participant Sidecar as BrowserSidecar
    participant C as Cliente HTTP

    W->>HI: start(timeout_s=240)
    loop heartbeat cada 15 s
        HI-->>W: heartbeat
        HI->>Sidecar: ping
    end

    Note over HI: pasan 240 s sin signal
    HI-->>W: ApplicationError(kind=human_input_timeout, non_retryable=true)
    W->>Sidecar: terminate
    W->>W: state = failed (reason=human_input_timeout)
    W-->>C: webhook job.failed{reason: human_input_timeout, field_key, expires_at}
```

## Reconexión tras crash del worker

Idéntico al patrón de OTP (ver `otp-pause-resume.md`):

1. Worker que corre `HumanInputAwaitActivity` muere → Temporal detecta heartbeat lost.
2. Nuevo worker recibe `browser_session_token` + `field_key_pending` desde activity input.
3. Conecta al Unix socket del sidecar (`socket_path`).
4. Sidecar sobrevivió — browser context y CDP siguen vivos.
5. Nuevo worker reanuda heartbeat loop. La signal `human_input_provided` que ya estaba pendiente en Temporal queue se entrega normalmente.

Taxonomía de fallos durante human input wait — heredada de ADR-0019:

| Error interno | Causa | Resultado externo |
|---|---|---|
| `sidecar_unreachable` | Unix socket no responde | job `failed` reason `browser_lost` |
| `browser_lost` | Sidecar vivo, Chromium muerto | job `failed` reason `browser_lost` |
| `human_input_timeout` | Sin signal en `timeout_s` | job `failed` reason `human_input_timeout` |
| `human_input_rejected` | Respuesta entregada pero banco la rechaza | job `failed` reason `human_input_rejected`; cache invalidada |
| `human_input_invalid` | API rechazó el POST (length/charset) | el POST devuelve 400; el job sigue esperando hasta timeout |

## Sub-estado `human_input_required`

```mermaid
stateDiagram-v2
    [*] --> question_extracted: runner alcanza step prompt_user
    question_extracted --> cache_lookup: hash(bank + cred + field_key + question)
    cache_lookup --> auto_filled: hit
    cache_lookup --> awaiting_answer: miss → webhook emitido
    awaiting_answer --> verifying: signal human_input_provided
    auto_filled --> verifying: fill ya hecho
    verifying --> resumed: banco acepta (next step ok)
    verifying --> failed_rejected: banco rechaza (assertion_failed)
    awaiting_answer --> failed_timeout: 240s sin signal
    awaiting_answer --> failed_browser_lost: sidecar/browser muere
    failed_rejected --> [*]
    failed_timeout --> [*]
    failed_browser_lost --> [*]
    resumed --> [*]
```

## Idempotency del signal

`POST /jobs/{id}/human-input` es **idempotente por `(job_id, field_key)`**:

- Si job está en `human_input_required` para ese `field_key` → entrega signal, devuelve `204`.
- Si ya pasó (signal previa exitosa) → `204` no-op.
- Si `field_key` no coincide con el prompt actual → `409` con `expected_field_key` en el error body.
- Si job en `failed` por timeout → `410 Gone` con `reason: human_input_expired`.
- Si job no existe → `404`.

Múltiples llamadas concurrentes con el mismo `(job_id, field_key)` son seguras: el cap de 3 intentos del rate-limiter cubre el caso de retry honesto del cliente; Temporal ignora signals de más una vez que la activity completó.

## Distinción explícita: `prompt_user` no es OTP

| Aspecto | OTP (`pause_for_otp: true`) | `prompt_user` step |
|---|---|---|
| Mecanismo | flag sobre step | step type explícito |
| Valor del usuario | ninguno (signal vacío) | `answer: str` (texto) |
| Endpoint | `POST /jobs/{id}/otp-confirmed` | `POST /jobs/{id}/human-input` |
| Signal | `otp_confirmed` (sin payload) | `human_input_provided{field_key, answer, persist}` |
| Webhook | `job.otp_required` | `job.human_input_required` |
| Activity | `OTPSignalAwaitActivity` | `HumanInputAwaitActivity` |
| Cache | n/a (OTP es always-fresh) | sí, vault `security_q`, TTL 90d |
| Hard cap | 4 min | configurable per-step (`timeout_s`, default 240s) |

Comparten: `BrowserSidecar` (ADR-0019), determinismo del workflow, taxonomía de fallos de browser.

## Métricas críticas

- `human_input_wait_duration_seconds` (histograma).
- `human_input_timeout_total` (counter).
- `human_input_cache_hit_ratio` (gauge).
- `human_input_cache_invalidated_total` (counter, alarm si sube — indica respuestas stale o banco con preguntas rotadas).
- `human_input_attempts_per_field_key` (histograma) — alarm si p99 > 1.
- `browser_lost_during_human_input_total` (counter).

## Referencias

- ADR-0021 — Decisión del step type: [`../adr/0021-human-input-step-type.md`](../adr/0021-human-input-step-type.md)
- ADR-0019 — BrowserSidecar: [`../adr/0019-browser-sidecar-otp.md`](../adr/0019-browser-sidecar-otp.md)
- Flow OTP análogo: [`./otp-pause-resume.md`](./otp-pause-resume.md)
- Endpoint: [`../02-components/api.md`](../02-components/api.md) sección `POST /jobs/{id}/human-input`
- Threat T27: [`../04-security/threat-model.md`](../04-security/threat-model.md)
- Linter L15: [`../04-security/community-maps.md`](../04-security/community-maps.md)
