# Flow: OTP Pause / Resume (Clave Móvil)

Flujo crítico del producto. Banco General **no usa SMS**: envía un push a la app del banco del cliente y espera tap manual. La sesión bancaria expira a ~5 min. El workflow debe pausar de forma durable, mantener el browser context vivo, y reanudar dentro del hard cap de 4 min, o abortar limpio.

**Nota de implementación (ADR-0019):** El browser context sobrevive a la muerte del worker gracias al `BrowserSidecar`, un proceso dentro del sandbox container que mantiene la conexión CDP independientemente del worker Temporal. Ver [`adr/0019-browser-sidecar-otp.md`](../adr/0019-browser-sidecar-otp.md).

## Sequence diagram

```mermaid
sequenceDiagram
    autonumber
    participant C as Cliente
    participant API as FastAPI
    participant W as ScrapeJobWorkflow
    participant Login as LoginActivity
    participant OTP as OTPSignalAwaitActivity
    participant Sidecar as BrowserSidecar (Unix socket)
    participant Sand as Sandbox + Chromium
    participant Bank as Banco web
    participant App as App banco (móvil del cliente)

    W->>Login: start
    Login->>Sidecar: spawn sidecar (conecta CDP a Chromium)
    Sidecar->>Sand: establece WebSocket CDP
    Login->>Sand: navega login + fill creds (vía Sidecar)
    Sand->>Bank: submit
    Bank-->>Sand: pantalla "Confirme en su Clave Móvil"
    Bank-->>App: push notification
    Sand-->>Sidecar: detected step "otp_required"
    Sidecar-->>Login: step detectado
    Login-->>W: ApplicationError(non_retryable=false, kind=needs_otp) + browser_session_token{container_id, socket_path, sidecar_pid}

    W->>W: state = otp_required
    W-->>C: webhook job.otp_required {push_sent_at, deadline}
    Note over App,C: cliente recibe push y abre la app

    W->>OTP: start (browser_session_token, hard_cap=4min)
    loop cada 15 s
        OTP->>Sidecar: heartbeat-ping (Unix socket)
        Sidecar-->>OTP: pong (CDP connection alive)
        OTP-->>W: heartbeat Temporal (mantiene activity slot viva)
    end

    Note over Sidecar: Sidecar mantiene CDP activo independientemente del worker

    App->>App: cliente toca "Aprobar"
    Bank-->>Sand: backend valida push, redirige a dashboard
    Note over App,API: cliente vuelve a su integración y confirma
    C->>API: POST /jobs/{id}/otp-confirmed
    API->>W: signal otp_confirmed
    W->>OTP: deliver signal
    OTP->>Sidecar: verify dashboard reachable (canary selector)
    Sidecar->>Sand: evalúa selector canary
    Sand-->>Sidecar: dashboard accesible
    Sidecar-->>OTP: ok
    OTP->>Sidecar: terminate (cleanup limpio)
    OTP-->>W: ok, sesión vigente
    W->>W: state = resumed → running
    W-->>C: webhook job.progress (login_done)

    Note over W: sigue con NavigateActivity + download Excel...
```

## Reconexión tras crash del worker (worker restart)

Si el worker Temporal muere durante `OTPSignalAwaitActivity`, el `BrowserSidecar` sigue corriendo dentro del sandbox container y mantiene la conexión CDP activa.

```mermaid
sequenceDiagram
    autonumber
    participant W1 as Worker (crashed)
    participant Temporal as Temporal Server
    participant W2 as Worker (nuevo)
    participant OTP as OTPSignalAwaitActivity (nueva instancia)
    participant Sidecar as BrowserSidecar (superviviente)
    participant Sand as Chromium (superviviente)

    Note over W1: worker muere durante heartbeat loop
    W1-xTemporal: heartbeat perdido
    Temporal->>Temporal: heartbeat_timeout superado → reschedule activity
    Temporal->>W2: schedule OTPSignalAwaitActivity (mismo input: browser_session_token)

    W2->>OTP: start (browser_session_token con socket_path)
    OTP->>Sidecar: heartbeat-ping (conecta al Unix socket existente)
    Note over Sidecar: Sidecar sobrevivió — CDP sigue vivo
    Sidecar-->>OTP: pong
    Note over OTP: reconexión exitosa, reanuda loop normal
    loop cada 15 s
        OTP->>Sidecar: heartbeat-ping
        Sidecar-->>OTP: pong
        OTP-->>Temporal: heartbeat
    end
```

Si el sidecar no responde al reconnect (`sidecar_unreachable`), `OTPSignalAwaitActivity` falla inmediatamente con `ApplicationError(non_retryable=true)` → job `failed` con `reason: browser_lost`.

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

- En workflow event history: `browser_session_token` — contiene `{container_id, socket_path, sidecar_pid}`.
- En el sandbox container: el proceso de Chromium **y** el proceso `BrowserSidecar` que mantiene la conexión CDP activa.

**Responsabilidades separadas:**
- `OTPSignalAwaitActivity` mantiene vivo el **slot de signal de Temporal** (heartbeat cada 15 s contra Temporal Server).
- `BrowserSidecar` mantiene viva la **conexión CDP con Chromium** (WebSocket permanente dentro del container).

Estas son responsabilidades distintas sobre ciclos de vida distintos. El sidecar no depende del worker; el worker se reconecta al sidecar al reanudarse.

Si el worker que corre `OTPSignalAwaitActivity` muere:

1. Temporal detecta heartbeat lost (`heartbeat_timeout` superado).
2. Reschedule la activity en otro worker.
3. El nuevo worker recibe `browser_session_token` desde el activity input.
4. Conecta al Unix socket del sidecar (`socket_path`) — el sidecar sobrevivió dentro del container.
5. Envía `heartbeat-ping`; si el sidecar responde, la sesión CDP está viva.
6. Continúa el loop de heartbeat sin reiniciar el browser.

**Taxonomía de fallos durante OTP wait:**

| Error interno | Causa | Resultado externo |
|---|---|---|
| `sidecar_unreachable` | Unix socket no responde (sidecar murió) | job `failed` reason `browser_lost` |
| `browser_lost` | Sidecar vivo, pero Chromium CDP no responde | job `failed` reason `browser_lost` |
| `session_lost` | Sidecar + Chrome vivos, canary selector falla post-OTP | job `failed` reason `session_lost` |

Ver [ADR-0019](../adr/0019-browser-sidecar-otp.md) para el diseño completo del sidecar y su lifecycle.

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
- ADR de no-persistencia de sesión: [`adr/0015-no-session-persistence-v1.md`](../adr/0015-no-session-persistence-v1.md)
- **ADR BrowserSidecar**: [`adr/0019-browser-sidecar-otp.md`](../adr/0019-browser-sidecar-otp.md) — diseño del proceso sidecar que mantiene CDP vivo durante OTP wait
