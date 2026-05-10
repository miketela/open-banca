# ADR-0003: Temporal como motor de orquestación durable

## Context

`open-banca` orquesta scrape jobs que combinan tres características incompatibles con un job runner simple:

1. **Pausas largas y asincrónicas para input humano**: confirmación de Clave Móvil llega vía signal externo (`POST /jobs/{id}/otp-confirmed`) en cualquier momento dentro de un hard cap de 4 min. La sesión bancaria expira a ~5 min, así que el job no puede simplemente "reintentarse desde cero": tiene que mantener el browser context vivo durante la pausa.
2. **Recovery determinístico tras crash**: si el worker que ejecuta el job muere durante la pausa de OTP, otro worker debe poder retomar exactamente el mismo punto, re-asociarse al sandbox que ya tiene el browser logueado, y recibir la signal cuando llegue.
3. **Composición de actores heterogéneos**: cada job mezcla Playwright determinístico, agentes LLM caros (Mapper/Remapper con Claude vision), agentes texto baratos (Validator/Judge con DeepSeek), y entrega de webhooks con retries propios. Cada uno con timeouts, backoffs y reglas de idempotency distintas.

Adicionalmente, los flujos de remap incluyen `human_required` que puede esperar hasta 24 h por aprobación de un operador. Esto es trivialmente "long-running" en el sentido tradicional.

## Decision

Adoptar **Temporal** como motor de durable execution. El componente `adapters/orchestrator/` define:

- Un workflow raíz `ScrapeJobWorkflow` por cada `POST /scrape`.
- Child workflows `MapBankWorkflow` y `RemapBankWorkflow` para procesos LLM-pesados.
- Activities por cada interacción externa: login, navegación, descarga, parse, validate, judge, mapper, remapper, webhook, espera de OTP signal.
- Signals para input asincrónico: `otp_confirmed`, `remap_approved`, `cancel_job`.

Self-hosting implica que `docker-compose up` levanta como mínimo: API (FastAPI) + Temporal server + 1+ Temporal worker + 1 sandbox runner por job activo. No es un binario único.

## Consequences

### Positivas

- **OTP pause/resume nativo**: signals + activity heartbeats resuelven el problema crítico sin código custom de state machine. El workflow espera la signal con primitivas determinísticas; si el worker muere, otro lo retoma.
- **Retries built-in con políticas declarativas**: backoff exponencial, max attempts, non-retryable error types se configuran por activity; no hay que escribir loops ni jitter.
- **Time-skipping testing**: `WorkflowEnvironment` permite testear flujos de OTP timeout (4 min) y proposal expiration (24 h) en milisegundos, sin sleeps reales.
- **Replay determinístico**: dado el event history de un job, se puede re-ejecutar localmente para debug post-mortem. Crítico cuando un cliente reporta "el job de ayer se rompió raro".
- **Visibilidad operativa**: Temporal Web UI muestra estado de cada job, eventos, retries, errores. Reduce drásticamente el costo de soporte.
- **Cancelación limpia**: cancel_job signal propaga cancelación a activities en curso de forma controlada (cleanup de browser, liberar sandbox).
- **Composabilidad**: child workflows aíslan la complejidad del Mapper/Remapper sin contaminar el workflow raíz.

### Negativas / trade-offs aceptados

- **Peso operativo**: el deployment mínimo agrega 2+ contenedores (Temporal server + worker) sobre lo que sería un proceso único. El operador self-host debe entender qué es Temporal antes de operarlo.
- **Curva de aprendizaje**: el equipo (y futuros contribuidores) debe internalizar reglas de determinismo (no `datetime.now()`, no random, no I/O directo en workflows). Anti-patterns se filtran fácil sin disciplina.
- **Deployment más complejo**: backups del Temporal server, persistencia (Postgres/MySQL/SQLite — Temporal soporta), tuning de namespace y retention. No es invisible.
- **Versionado de workflows**: cambios al código de un workflow en producción requieren `workflow.get_version()` o estrategia de drainado. Ignorarlo rompe replay de jobs en vuelo.
- **Acoplamiento al contrato Temporal**: si en algún momento se quiere migrar fuera de Temporal, hay que reimplementar workflows + activities + signals. La inversión es real.

### Mitigaciones

- README de operación documenta el `docker-compose` mínimo y enlaza a la doc oficial de Temporal.
- Tests obligatorios con `WorkflowEnvironment` para todos los workflows críticos antes de merge.
- Helper interno que prohibe imports no determinísticos en `adapters/orchestrator/workflows/` (lint rule custom).
- Política de versionado: cambios incompatibles bumpean `task_queue` (ej. `scrape-v1` → `scrape-v2`) y conviven workers de ambas versiones durante drainado.

## Alternatives Considered

### Alt 1: SQLite + cola simple + browser context serializado a disco

Modelo: tabla `jobs` con estado, worker process loopea, descarga el browser context (cookies + session storage) a disco antes de pausar, lo restaura al recibir el signal de OTP.

- **Pros**: 1 binario, 0 servicios extra, deployment trivial.
- **Contras decisivos**:
  - Hay que reimplementar state machine, retries, backoff, signals, idempotency, recovery, replay — código custom propenso a bugs justo en el camino crítico (OTP).
  - Browser context serializado != browser vivo. Restaurarlo no garantiza que la sesión bancaria siga válida (las cookies viven, pero estado en memoria del cliente JS y conexiones websocket no). En la práctica el banco re-pide login porque detecta cambio de fingerprint.
  - Sin replay determinístico, debug post-mortem requiere reproducir credenciales reales — flujo prohibido en producción.
  - Sin time-skipping, tests de OTP timeout y proposal expiration son inviables en CI o cuestan minutos por test.
- **Veredicto**: descartado. El ahorro operativo no compensa el riesgo de bugs en el camino crítico.

### Alt 2: Celery + Redis con tasks de larga duración

- **Pros**: stack Python conocido, retries built-in.
- **Contras**: signals asincrónicas no son nativos; workers no sobreviven crash sin perder estado intermedio; no hay event history ni replay; tasks de 4 min bloqueando workers escalan mal.
- **Veredicto**: descartado. Mismo problema que SQLite simple, con más infraestructura.

### Alt 3: AWS Step Functions / GCP Workflows

- **Pros**: durable execution, signals (callbacks).
- **Contras**: rompe el principio self-host del producto. Vendor lock-in.
- **Veredicto**: descartado por incompatibilidad con la tesis del proyecto.

## Implicación para self-hosting

El operador debe:

1. Levantar `docker-compose` con servicios: `api`, `temporal`, `temporal-worker` (1+), `sandbox-runner` (creado por job, no servicio fijo).
2. Persistir el storage de Temporal (volumen Postgres o SQLite según config).
3. Exponer Temporal Web UI sólo en intranet (no público, contiene metadatos sensibles).
4. Backupear el state de Temporal junto con el sqlcipher de open-banca.

La doc de operaciones (`05-operations/`) cubre detalle de runbook, tuning y backup.

## Cross-references

- **ADR-0019** — [BrowserSidecar](./0019-browser-sidecar-otp.md): el mecanismo concreto que hace ejecutable la garantía "crash del worker durante OTP pause no pierde el job" (punto 2 del Context y Consequences > Positivas). La activity `OTPSignalAwaitActivity` mantiene el slot Temporal; el sidecar mantiene la conexión CDP.

## Status

Accepted (2026-05-09)
