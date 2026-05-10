# Flow: OTP Pause / Resume (Clave Móvil)

Flujo crítico del producto. Banco General **no usa SMS**: envía un push a la app del banco del cliente y espera tap manual. La sesión bancaria expira a ~5 min. El workflow debe pausar de forma durable, mantener el browser context vivo, y reanudar dentro del hard cap de 4 min, o abortar limpio.

## Sequence diagram

```mermaid
sequenceDiagram
    autonumber
    participant C as Cliente
    participant API as FastAPI
    participant W as ScrapeJobWorkflow
    participant Login as LoginActivity
    participant OTP as OTPSignalAwaitActivity
    participant Sand as Sandbox + Browser
    participant Bank as Banco web
    participant App as App banco (móvil del cliente)

    W->>Login: start
    Login->>Sand: navega login + fill creds
    Sand->>Bank: submit
    Bank-->>Sand: pantalla "Confirme en su Clave Móvil"
    Bank-->>App: push notification
    Sand-->>Login: detected step "otp_required"
    Login-->>W: ApplicationError(non_retryable=false, kind=needs_otp) + browser_session_token

    W->>W: state = otp_required
    W-->>C: webhook job.otp_required {push_sent_at, deadline}
    Note over App,C: cliente recibe push y abre la app

    W->>OTP: start (browser_session_token, hard_cap=4min)
    loop cada 15 s
        OTP->>Sand: heartbeat → browser sigue vivo
        OTP-->>W: heartbeat (mantiene activity viva)
    end

    App->>App: cliente toca "Aprobar"
    Bank-->>Sand: backend valida push, redirige a dashboard
    Note over App,API: cliente vuelve a su integración y confirma
    C->>API: POST /jobs/{id}/otp-confirmed
    API->>W: signal otp_confirmed
    W->>OTP: deliver signal
    OTP->>Sand: verify dashboard reachable (canary selector)
    OTP-->>W: ok, browser_session_token sigue vigente
    W->>W: state = resumed → running
    W-->>C: webhook job.progress (login_done)

    Note over W: sigue con NavigateActivity + download Excel...
```

## Rama de timeout

```mermaid
sequenceDiagram
    autonumber
    participant C as Cliente
    participant API as FastAPI
    participant W as ScrapeJobWorkflow
    participant OTP as OTPSignalAwaitActivity
    participant Sand as Sandbox + Browser

    W->>OTP: start (hard_cap=4min)
    loop heartbeat cada 15 s
        OTP-->>W: heartbeat
    end

    Note over OTP: pasan 4 min sin signal otp_confirmed
    OTP-->>W: ApplicationError(kind=otp_timeout, non_retryable=true)
    W->>Sand: cleanup: cierra browser context, libera sandbox
    W->>W: state = failed (reason=otp_timeout)
    W-->>C: webhook job.failed {reason: otp_timeout, push_sent_at, expired_at}

    Note over C,API: cliente puede reintentar con nuevo POST /scrape (no resume sobre el mismo job)
```

## Por qué hard cap = 4 min y no 5

La sesión bancaria expira ~5 min de inactividad. Si el workflow espera los 5 minutos completos y el signal llega justo al borde, el browser puede haber perdido la sesión silenciosamente y el dashboard que sigue es una redirección al login. Hard cap a 4 min deja 60 s de buffer para verificar canary selector + ejecutar el siguiente NavigateActivity antes de que el banco corte.

## Mantenimiento del browser context

Temporal **no serializa el browser context** (Chrome no es serializable). Lo que sí persiste:

- En workflow event history: `browser_session_token` (handle al sandbox container).
- En el sandbox container: el proceso de Chrome con el contexto vivo en memoria.

`OTPSignalAwaitActivity` envía heartbeats cada 15 s contra Temporal. Cada heartbeat también pinguea el sandbox para confirmar que el container está vivo y el browser responde. Si el worker que corre la activity muere:

1. Temporal detecta heartbeat lost (`heartbeat_timeout` superado).
2. Reschedule la activity en otro worker.
3. El nuevo worker recibe `browser_session_token` desde el activity input.
4. Se re-asocia al sandbox container existente (que sobrevivió al worker porque vive en otro proceso/host).
5. Continúa el loop de heartbeat sin reiniciar el browser.

Si el sandbox container también murió: la activity falla con `ApplicationError(kind=browser_lost, non_retryable=true)` y el job va a `failed` con razón `session_lost`.

## Sub-estado `otp_required` (detalle)

```mermaid
stateDiagram-v2
    [*] --> push_sent: Login detecta paso Clave Móvil
    push_sent --> awaiting_signal: webhook job.otp_required emitido
    awaiting_signal --> verifying: signal otp_confirmed recibida
    verifying --> resumed: canary selector ok (dashboard accesible)
    verifying --> failed_session_lost: canary selector falla (sesión murió)
    awaiting_signal --> failed_otp_timeout: hard cap 4 min
    push_sent --> failed_browser_lost: sandbox/browser muere antes de signal
    awaiting_signal --> failed_browser_lost: sandbox/browser muere antes de signal
    resumed --> [*]
    failed_otp_timeout --> [*]
    failed_session_lost --> [*]
    failed_browser_lost --> [*]
```

## Idempotency del signal

`POST /jobs/{id}/otp-confirmed` es idempotente por estado:

- Si job está en `otp_required` → entrega signal, devuelve `204`.
- Si job ya pasó a `running` (signal previa exitosa) → `204` no-op.
- Si job está en `failed` por timeout → `410 Gone` con `reason: otp_expired`.
- Si job no existe → `404`.

Múltiples llamadas concurrentes con el mismo `job_id` son seguras: Temporal entrega la primera signal, las siguientes son ignoradas porque `OTPSignalAwaitActivity` ya completó.

## Cancelación durante OTP

Si el cliente envía `POST /jobs/{id}/cancel` mientras el job está en `otp_required`:

1. API entrega signal `cancel_job` al workflow.
2. Workflow cancela `OTPSignalAwaitActivity`.
3. Cleanup: cerrar browser context + liberar sandbox.
4. Webhook `job.failed` con `reason: cancelled_by_user`.

## Métricas críticas

- `otp_wait_duration_seconds` (histograma) — cuánto tarda el humano en confirmar.
- `otp_timeout_total` (counter) — cuántos jobs fallan por hard cap.
- `browser_lost_during_otp_total` (counter) — alarma si sube.
- `otp_signal_idempotent_noop_total` — tracking de doble-tap del cliente.

## Referencias

- Componente: [`02-components/orchestrator.md`](../02-components/orchestrator.md)
- Endpoint: [`02-components/api.md`](../02-components/api.md) sección `POST /jobs/{id}/otp-confirmed`
- ADR de no-persistencia de sesión: `adr/0015-no-session-persistence-v1.md` (existente)
