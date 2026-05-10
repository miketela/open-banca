# ADR-0019 — BrowserSidecar: proceso dedicado que mantiene la sesión Playwright/CDP durante OTP wait

## Context

ADR-0003, ADR-0015 y el flow `03-flows/otp-pause-resume.md` describen una arquitectura en la que el browser context sobrevive a la muerte del worker Temporal: si el worker muere durante `OTPSignalAwaitActivity`, otro worker puede "re-asociarse al sandbox container existente" vía `browser_session_token`.

**Este supuesto no es ejecutable tal como está documentado.**

El problema concreto: la conexión CDP (Chrome DevTools Protocol) es un WebSocket mantenido por el proceso cliente de Playwright. Ese proceso cliente vive *dentro del worker*. Cuando el worker muere, el WebSocket CDP se cierra. Chrome sigue corriendo dentro del sandbox container, pero no hay ningún proceso que sostenga la conexión — el browser context de Playwright se destruye con el worker. Un nuevo worker no puede "re-asociarse" a un browser context que ya no existe en memoria, aunque Chrome siga corriendo.

Consecuencia directa: en el estado actual del diseño, cualquier muerte del worker durante el período de espera OTP resulta en `browser_lost`, obligando al job a `failed` y requiriendo que el usuario inicie un nuevo scrape desde cero. Esto contradice la garantía declarada en ADR-0003 ("crash del worker durante OTP pause no pierde el job").

**Solución requerida:** un proceso independiente dentro del sandbox container que mantenga la conexión CDP viva y que sobreviva a ciclos de vida del worker Temporal.

### Restricciones de diseño

- El sandbox container es efímero por diseño (ADR-0009). No introducimos contenedores long-lived extra.
- Playwright no tiene un modo "server proxy" nativo que persista contextos entre conexiones de clientes; hay que construirlo.
- El tiempo de espera de OTP tiene hard cap de 4 minutos (ADR-0003/flow). El sidecar no necesita vivir más de `hard_cap + buffer` (~5 min).
- La comunicación worker↔sidecar no cruza límites de host (worker y sandbox co-residen en el mismo host en v1); no se necesita TLS inter-host.

## Decision

**Introducimos `BrowserSidecar`, un proceso ligero dentro del mismo sandbox container que Chrome**, cuya única responsabilidad es mantener una conexión CDP activa e implementar un proxy de comandos Playwright sobre Unix Domain Socket.

### Arquitectura del sidecar

```
Sandbox container (Docker, efímero, por-job)
├── chromium (headless)             ← proceso existente
└── browser_sidecar                 ← NUEVO proceso
    ├── Playwright Page + Context   ← mantiene CDP WebSocket con chromium
    ├── Unix socket: /run/banca/sidecar.sock
    └── TTL watchdog: auto-termina a (hard_cap + 60 s)
```

El worker Temporal se comunica con el sidecar a través de un **Unix Domain Socket** (`/run/banca/sidecar.sock`). El sidecar expone un protocolo mínimo de comandos (navigate, fill, click, screenshot, heartbeat-ping, terminate).

### `browser_session_token` redefinido

El `browser_session_token` ya no es "handle al sandbox container". A partir de este ADR, es una estructura opaca que contiene:

- `container_id`: ID del Docker container del sandbox (para lifecycle checks).
- `socket_path`: ruta del Unix socket del sidecar (`/run/banca/sidecar.sock`).
- `sidecar_pid`: PID del proceso sidecar (para verificación de liveness al reconectar).

El worker resuelve este token antes de cada operación sobre el browser; si el socket no responde, falla fast con el error apropiado (ver taxonomía de fallos más abajo).

### Lifecycle del sidecar

El sidecar tiene **tres capas de terminación**, en orden de precedencia:

1. **TTL self-watchdog (primario)**: el sidecar inicia un timer al arrancar. Si el timer expira (`hard_cap + 60 s` = 5 min), se auto-termina limpiamente (cierra el Page, cierra el Context, cierra el WebSocket CDP, exit 0). Esta capa es la principal: garantiza que no queden sidecars huérfanos aunque Temporal abandone la retentativa.

2. **Terminate-on-cleanup (secundario)**: cuando el worker retoma `OTPSignalAwaitActivity` después de un crash, o cuando el job pasa a `failed`/`cancelled`, el worker envía el comando `terminate` al sidecar vía Unix socket antes de salir. Esto permite un shutdown limpio antes de que el container se destruya.

3. **Orphan reaper a nivel container (terciario)**: al destruir el sandbox container (fin de `ScrapeJobWorkflow`), Docker kill propaga SIGTERM/SIGKILL a todos los procesos del container, incluyendo el sidecar. Es el backstop final.

### Arranque del sidecar

El sidecar arranca inmediatamente después de que `LoginActivity` detecta el paso `otp_required`, antes de retornar al workflow. En ese momento el browser ya tiene la sesión bancaria activa y está en la pantalla de "Confirme en su Clave Móvil". El sidecar:

1. Hereda la conexión CDP del `LoginActivity` vía `browser_session_token` provisional generado en-proceso.
2. Establece su propio WebSocket CDP independiente con chromium.
3. Crea el Unix socket y empieza a escuchar comandos.
4. Inicia el TTL watchdog.
5. Retorna `browser_session_token` (con `socket_path`) al worker.

El worker devuelve este token al workflow en el `ApplicationError(kind=needs_otp)`.

### Reconnect tras worker crash

Cuando el worker Temporal reanuda `OTPSignalAwaitActivity` después de un crash:

1. Recibe `browser_session_token` desde el activity input (persistido en Temporal event history).
2. Conecta al Unix socket del sidecar (`socket_path`).
3. Envía `heartbeat-ping`; si el sidecar responde, la sesión está viva.
4. Reanuda el loop de heartbeat normal.

El sidecar no sabe ni le importa si el worker murió. Sigue manteniendo la conexión CDP independientemente.

### Taxonomía de fallos durante OTP wait

Con el sidecar, los fallos tienen tres categorías distintas:

| Error | Significado | Acción |
|---|---|---|
| `sidecar_unreachable` | Unix socket no responde; sidecar murió (crash inesperado o TTL expirado). Chrome puede estar vivo o muerto. | `ApplicationError(non_retryable=true)` → job `failed` reason `browser_lost` |
| `browser_lost` | Sidecar responde pero informa que Chrome (CDP) no responde. | `ApplicationError(non_retryable=true)` → job `failed` reason `browser_lost` |
| `session_lost` | Chrome vivo, sidecar vivo, pero canary selector post-OTP falla (sesión bancaria expiró en el banco). | `ApplicationError(non_retryable=true)` → job `failed` reason `session_lost` |

`sidecar_unreachable` y `browser_lost` se exponen hacia arriba ambos como `reason: browser_lost` en la API externa (el cliente no necesita distinguir).

## Consequences

### Positivas

- **Garantía de supervivencia real**: el browser context ahora sobrevive genuinamente a la muerte del worker. La promesa de ADR-0003 pasa de aspiracional a ejecutable.
- **Sin cambio de sandbox model**: el sidecar vive dentro del container efímero existente (ADR-0009). No se introduce ningún container long-lived extra.
- **Separación de responsabilidades clara**: el worker Temporal gestiona el *slot de signal* (heartbeat Temporal); el sidecar gestiona la *conexión CDP*. Son ciclos de vida distintos, correctamente desacoplados.
- **Failfast limpio**: si el sidecar murió, el error se detecta inmediatamente al reconectar, sin intentos ciegos de re-login.

### Negativas / trade-offs

- **Proceso adicional por job**: cada job OTP añade un proceso sidecar dentro del container. Overhead mínimo (~10 MB RSS para el proceso, la conexión CDP ya existía). Sólo activo durante la ventana OTP, no durante todo el job.
- **Complejidad de arranque en LoginActivity**: `LoginActivity` debe ahora orquestar el spawn del sidecar, la transferencia de la conexión CDP, y la generación del nuevo `browser_session_token`. Si este paso falla, el job falla inmediatamente (antes, al menos, había un proceso de "intento de continuar").
- **Protocolo IPC a mantener**: el protocolo del Unix socket es código custom. Requiere tests de integración propios (se puede cubrir con pytest + container de test).
- **`browser_session_token` es un breaking change de contrato interno**: cualquier código que interprete el token como "container ID" debe actualizarse. No hay API externa expuesta, sólo interno.
- **Diagrama macro desactualizado**: `docs/01-architecture/macro.md` no muestra el sidecar. Requiere una actualización del diagrama de containers en un PR separado (nota para agent-prd / tarea de documentación).

### Operativas

- El sidecar hereda el network namespace del container (network allowlist ya configurada). No requiere cambios en la política de red del sandbox.
- Logs del sidecar van a stdout del container, recogidos por el mismo mecanismo de logging del sandbox.
- El TTL del sidecar debe configurarse vía variable de entorno `BANCA_OTP_SIDECAR_TTL_S` (default: `300`), para permitir ajuste sin rebuild.
- Para tests: el sidecar puede arrancarse en modo mock (Unix socket que responde comandos prefijados) para los tests de integración de `OTPSignalAwaitActivity` sin necesidad de Playwright real.

## Alternatives Considered

### Alt 1 — Aceptar abort-on-crash (no hacer nada)

El job falla con `browser_lost` si el worker muere durante OTP wait. El usuario recibe `job.failed` y puede reintentar con un nuevo `POST /scrape`.

- **Por qué fue rechazado**: viola la garantía central de ADR-0003 ("crash del worker durante OTP pause no pierde el job"). La UX es inaceptable: el usuario ya aprobó el OTP en su app bancaria, recibe una notificación de fallo, y debe volver a empezar. Los bancos PA pueden interpretar múltiples OTP pushes seguidos como anomalía y bloquear la cuenta temporalmente.

### Alt 2 — Sidecar como contenedor Docker separado (container sibling)

Un container sibling que comparte el network namespace con el sandbox container principal, con comunicación por localhost TCP o gRPC en lugar de Unix socket.

- **Por qué fue rechazado**: complejidad operativa mayor (dos containers por job, lifecycle coordinado externamente), sin beneficio real vs. proceso-en-mismo-container. La necesidad de cruzar límites de host no existe en v1. Revisable en v2 si el modelo de deployment cambia (e.g. orquestación k8s con containers en pods separados).

### Alt 3 — Playwright Remote Browser (playwright-server)

Usar el modo server de Playwright (`playwright run-server`) para desacoplar el cliente del proceso del browser.

- **Por qué fue rechazado**: `playwright run-server` no persiste browser contexts entre desconexiones de clientes — cuando el cliente desconecta, el context se cierra. Requeriría un patch del servidor de Playwright o una librería adicional. La complejidad supera la de construir el sidecar mínimo.

### Alt 4 — Serializar estado del browser a disco antes de que el worker muera

Guardar cookies + session storage a disco cifrado, restaurar en el nuevo worker.

- **Por qué fue rechazado**: analizado en ADR-0015. La restauración de estado no garantiza que la sesión bancaria siga válida (estado en memoria JS, conexiones WebSocket del banco, fingerprint). En la práctica el banco re-pide login, anulando el beneficio.

## References

- ADR-0003: [`0003-temporal-orchestration.md`](./0003-temporal-orchestration.md) — garantía de recovery tras crash de worker (sección Consequences > Positivas y sección Mitigaciones)
- ADR-0009: [`0009-docker-sandbox-per-job.md`](./0009-docker-sandbox-per-job.md) — sandbox efímero por job
- ADR-0015: [`0015-no-session-persistence-v1.md`](./0015-no-session-persistence-v1.md) — por qué no persistimos sesión entre jobs (cross-ref bidireccional)
- Flow: [`03-flows/otp-pause-resume.md`](../03-flows/otp-pause-resume.md) — sequence diagram actualizado con sidecar
- Componente: [`02-components/orchestrator.md`](../02-components/orchestrator.md)

## Status: Accepted (2026-05-10)
