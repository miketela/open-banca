# ADR-0011 — Webhook events HTTP POST con firma HMAC-SHA256

## Contexto

El cliente de la API necesita enterarse de eventos asíncronos: `job.created`, `job.otp_required`, `job.completed`, `job.failed`, `job.remap_proposed`, `job.human_required`. Especialmente `job.otp_required` es time-sensitive (ventana de ~5 min antes de que el banco corte la sesión).

Necesitamos un mecanismo de notificación que sea:

- Operacionalmente trivial de operar self-hosted.
- Seguro contra spoofing por terceros que adivinen la URL.
- Resistente a fallos transitorios del cliente.
- Estándar de mercado, fácil de implementar en cualquier lenguaje.

## Decisión

**Webhooks HTTP POST firmados con HMAC-SHA256 + timestamp anti-replay**, modelado sobre los patterns de Stripe y GitHub.

Detalles:

- Header `X-OpenBanca-Signature: t=<unix_ts>,v1=<hex_hmac>` donde `hmac = HMAC-SHA256(secret, ts + "." + raw_body)`.
- Headers acompañantes: `X-OpenBanca-Event`, `X-OpenBanca-Delivery` (UUID idempotencia).
- Cliente valida: ventana de timestamp ±5 min, HMAC constant-time compare, dedup por `Delivery`.
- Retry: backoff exponencial con jitter (15s, 1m, 5m, 30m, 2h, 6h), 6 attempts, ~24h. 5xx + 408 + 429 retriables; 4xx restantes parkean a DLQ.
- DLQ persistido 7 días con replay manual via endpoint admin.
- Webhook URLs sólo aceptan `https://` (validado al registrar).
- Rotación de secret con dos secretos vivos en una ventana de gracia.

Detalle: [`02-components/webhooks.md`](../02-components/webhooks.md).

## Alternativas consideradas

### Server-Sent Events (SSE) en endpoint `/events`
- **Rechazada para v1**: requiere conexión persistente del cliente, cambia el modelo operativo (load balancers con sticky sessions, timeouts), añade complejidad de reconnect logic.
- Trade-off perdido: latencia más baja, sin retries.
- **Re-evaluable v2** si emerge demanda.

### WebSocket bidireccional
- **Rechazada**: aún más operativamente pesado que SSE; sobredimensionado para un flujo donde el cliente sólo recibe.

### Polling sólo (`GET /jobs/:id`)
- **Coexiste**: el endpoint existe como fallback. Pero polling solo no resuelve `job.otp_required` con baja latencia y obliga al cliente a poll con frecuencia alta para no perder la ventana de 5 min. Webhook + polling es el patrón final.

### gRPC streaming
- **Rechazada**: añade dependencia de protobuf/gRPC en el cliente. open-banca apunta a HTTP REST plain como contrato.

### Firma con asymmetric (RSA / Ed25519) en lugar de HMAC
- **Rechazada para v1**: HMAC es trivial de implementar en cualquier lenguaje y suficiente para autenticidad cuando el secret se rota. Asymmetric beneficia auditabilidad pero complica key distribution.
- Re-evaluable si emerge requisito de no-repudiation fuerte.

### Sin firma, sólo URL secreta
- **Rechazada**: una vez la URL leak (logs del proxy, intermediarios), cualquiera puede inyectar payloads. HMAC defiende incluso si la URL leak.

## Consecuencias

Positivas:

- Patrón conocido (Stripe, GitHub, Slack): cliente puede copiar implementación de samples existentes.
- Trivial server-side: HMAC-SHA256 está en la stdlib de todos los lenguajes mainstream.
- Anti-replay por timestamp ±5 min + dedup por `Delivery` ID.
- Retries built-in con DLQ para resilencia frente a downtime del cliente.
- Self-hosted limpio: no requiere broker (Kafka, NATS) extra.

Negativas / costos:

- Latencia de entrega depende del cliente (DNS, TLS, proceso). En reintentos podemos llegar al límite de 5 min del banco para OTP. Mitigación: el primer intento de `job.otp_required` se hace inmediato y sin demora.
- Cliente que no valida HMAC es vulnerable a spoofing — responsabilidad documentada y sample code en SDKs.
- Reloj del cliente desfasado > 5 min rechaza todas las entregas. Documentado en runbook.
- DLQ overflow si cliente está caído mucho tiempo. 7 días configurable.

Detalle operativo: [`02-components/webhooks.md`](../02-components/webhooks.md). Threat model: [`04-security/threat-model.md`](../04-security/threat-model.md) (T09, T16).

## Status

Accepted (2026-05-09)
