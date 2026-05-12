# ADR-0021 — Step type `prompt_user`: input humano genérico mid-scrape

## Context

Banco General — y por información del operador, otros bancos PA — puede solicitar **preguntas de seguridad rotantes** entre el step de login (user+pass) y la fase de Clave Móvil. Ejemplos: "¿Nombre de su primera mascota?", "¿Color favorito de su madre?". Estas preguntas no estaban contempladas en el diseño original del Scraper Runner.

La arquitectura actual cubre **un** tipo de pausa-y-reanudación: OTP por Clave Móvil. El mecanismo se basa en un flag `pause_for_otp: true` sobre un step (ver `mapper-agent.md` y `03-flows/otp-pause-resume.md`), combinado con `OTPSignalAwaitActivity` y signal `otp_confirmed`. Funciona porque OTP es **out-of-band**: el usuario tap-aprueba en la app del banco y la web detecta la aprobación sola; el workflow solo necesita saber "el usuario terminó", no recibe ningún valor.

Una pregunta de seguridad rompe ese modelo: el sistema necesita **recibir un valor textual del usuario** (la respuesta), inyectarlo en un input específico del DOM, y avanzar. La superficie de información a transmitir es distinta:

- OTP: confirmación booleana (signal vacío).
- Pregunta de seguridad: `(field_key, question_text, answer_value)`.
- Captcha futuro: imagen/audio + token texto.
- Eventos de re-auth pasivos (banco re-pide password mid-sesión): similar a pregunta.

El diseño debe decidir si extiende el patrón de OTP (flag sobre step + side-channel especializado) o introduce un **step type genérico** que carga la semántica.

Restricciones:
- El runner es 0-LLM, declarativo. Cualquier step type debe ser ejecutable sin razonamiento.
- El runner ya tiene 9 step types (REQ-004). Añadir uno es barato; añadir tres (uno por cada vertiente futura) es ruido.
- El sidecar de ADR-0019 ya provee la primitiva "browser context sobrevive entre activities"; se puede reutilizar.
- Algunas preguntas se repiten entre logins (la madre del operador no cambia). Cachear respuestas reduce fricción.
- Algunas preguntas pueden rotar (banco sampleó de un pool). Empíricamente desconocido para Banco General — ver Open Questions.

Threat surface: el endpoint nuevo recibe **input arbitrario del cliente HTTP** que termina dentro del browser. Vector de injection (XSS, SQL fuera de scope, comandos de control char) y vector de account lockout (respuesta incorrecta cacheada).

## Decision

**Introducimos un nuevo step type `prompt_user` en el DSL del Scraper Runner**, ejecutado por una nueva activity `HumanInputAwaitActivity` análoga a `OTPSignalAwaitActivity`, sostenido por el mismo `BrowserSidecar` durante el wait, y resuelto por signal `human_input_provided` desde un endpoint nuevo `POST /jobs/{id}/human-input`.

**OTP no se reescribe**. El flag `pause_for_otp: true` permanece. La razón es semántica: OTP es out-of-band sin valor de retorno; `prompt_user` es in-band con valor textual. Forzarlos al mismo step type oculta esta diferencia detrás de campos opcionales. Se mantiene como dos patrones explícitos. Revisable en v1.x si se descubre evidencia de unificación útil.

### Forma del step

```json
{
  "step_type": "prompt_user",
  "step_id": "security_question",
  "selector": "#security-answer",
  "question_selector": ".security-question-text",
  "field_key": "security_q_mother_color",
  "cache_answers": true,
  "timeout_s": 240,
  "submit_selector": "button[type=submit]"
}
```

Campos obligatorios: `step_type`, `step_id`, `selector` (input donde se escribe la respuesta), `question_selector` (DOM node con el texto de la pregunta), `field_key` (identificador estable de qué se pregunta), `timeout_s`. Campos opcionales: `cache_answers` (default `true`), `submit_selector` (si el form requiere click post-fill).

`field_key` valida con regex `^[a-z][a-z0-9_]{2,32}$`. El field_key es semántica del operador del mapping (qué pregunta es, no qué respuesta) — el sistema no lo interpreta.

### Cache de respuestas

Namespace en el Secret Vault: `security_q`. Cache key compuesta:

```
sha256(
  bank_id || ":" ||
  credential_ref || ":" ||
  field_key || ":" ||
  normalize(question_text)
)
```

`normalize(question_text)`: lowercase + strip Unicode punctuation + collapse whitespace + NFC. La regla queda **pinned** en este ADR. Sin la normalización, la cache-hit rate depende de espaciado/acentuación del banco — no determinístico.

La composición incluye `bank_id` y `credential_ref` para evitar colisiones entre usuarios distintos del mismo self-host (caso multi-credencial). Incluye `field_key` para evitar colisión entre dos preguntas de un mismo banco para una misma credencial que coincidan léxicamente.

TTL del cache: **90 días por entrada** (`security_q_ttl_days`, configurable). Una pregunta de seguridad no cambia de respuesta excepto por intervención del usuario en el banco. 90 días es un compromiso entre comodidad y blast radius si el operador olvida que cacheó una respuesta vieja.

### Invalidación obligatoria en fallo

Si el step siguiente a `prompt_user` produce `BreakageEvent` con causa `assertion_failed` o `http_error` (señal típica de "respuesta incorrecta → banco te regresa al login"), la entrada de cache asociada se **invalida inmediatamente**. La activity `HumanInputAwaitActivity` registra el `cache_key` usado; el workflow lo borra del vault antes de propagar el fallo. Sin esta invalidación, una respuesta mal cacheada causaría loops de account lockout (mismo blast radius que T04).

### Endpoint, signal, payload

- `POST /jobs/{id}/human-input`. Body: `{ field_key: str, answer: str, persist: bool? = true }`. Response `204`.
- Signal Temporal: `human_input_provided` con payload `{ field_key: str, answer: str, persist: bool }`. La signal **siempre carga `field_key`** porque un workflow puede tener múltiples `prompt_user` steps; sin field_key la activity no puede correlacionar qué prompt se está resolviendo.
- Webhook nuevo `job.human_input_required`. Payload alto-nivel: `{ job_id, bank_id, field_key, question_text, cached_attempted: bool, expires_at }`.

### Validación de input (defensa contra T27 nuevo)

El endpoint API aplica antes de signal:
- Length cap: 256 bytes UTF-8.
- Character whitelist: Unicode printable + espacios. Rechaza control chars (categoría Cc), separators no estándar, embed direction marks.
- Rate limit: max 3 intentos por `(job_id, field_key)` (por sesión de wait).
- Audit log: registra `(job_id, field_key, attempt_n, sha256(answer)[0:8])`. **Nunca** registra la respuesta en claro.

### Lifecycle integrado con sidecar

`HumanInputAwaitActivity` reutiliza el `BrowserSidecar` (ADR-0019). El sidecar mantiene el browser context vivo durante el wait. El `browser_session_token` se extiende para llevar `current_prompt_field_key` y el TTL del sidecar pasa a `max(otp_ttl, human_input_ttl) + 60s` (default 360s) durante steps `prompt_user`.

### CLI Mapper pre-load

El Mapper CLI (corre con supervisión humana durante onboarding) detecta selectores con patrón typical de pregunta de seguridad (heurística + confirmación humana en terminal) y prompta al operador **en terminal** para preload de respuestas al vault con `field_key + question_text + answer`. Esto es **distinto** del runtime prompt: CLI es one-shot supervisado pre-deploy, runtime es webhook-driven mid-job. Ver `mapper-agent.md` sección "Security question detection".

## Consequences

### Positivas

- Patrón genérico que cubre preguntas de seguridad hoy, captcha texto en el futuro, re-auth password mid-sesión, sin proliferación de step types ad-hoc.
- Re-uso del sidecar (ADR-0019) — no añade infraestructura nueva.
- Cache opt-in con invalidación dura: reduce fricción sin riesgo silencioso de lockout.
- Threat surface acotado: un solo endpoint con whitelist character + length cap.

### Negativas

- 8º webhook event, 11ª activity, 13º endpoint API, 10º step type, 4ª signal. Más superficie a documentar y testear.
- Signal con payload (los otros 3 signals son vacíos o llevan IDs). Requiere actualizar contrato de Temporal Client.
- `field_key` es responsabilidad del autor del map. Map mal autorizado con `field_key` colisionando entre dos preguntas distintas produciría cross-contamination de cache. Mitigado por linter L15 + review oficial.
- Cache stale es plausible si banco rota preguntas en un pool sin invalidar; mitigado por TTL 90d + invalidación-en-fallo, pero residual existe.

### Operativas

- Variable env nueva: `BANCA_HUMAN_INPUT_TTL_DAYS` (default 90) para el TTL del cache `security_q`.
- El sidecar TTL operativo (`BANCA_OTP_SIDECAR_TTL_S`) se renombra conceptualmente a "sidecar TTL" — sigue cubriendo OTP wait + human input wait. Default sube de 300 a 360 para cubrir el peor caso de prompt humano lento.
- Documentación del operador debe ser explícita: si el cliente HTTP responde un `POST /jobs/{id}/human-input` con respuesta incorrecta, el job fallará y la cache se limpia. Reintentar requiere nuevo `POST /scrape`.
- Threat model añade T27.
- Linter community-maps añade L15.

## Alternatives Considered

### Alt 1 — OTP-only path (extender flag `pause_for_otp` a "pause genérico")

Reusar el flag actual y añadir un campo opcional `awaits_value: { field_key, question_selector }` cuando aplique.

**Rechazada.** Mezclar el caso "out-of-band sin valor" con "in-band con valor" en un mismo step camufla la diferencia semántica. Genera ambigüedad en validation: ¿qué hace el runner cuando `pause_for_otp=true` y `awaits_value` está presente — espera ambos? ¿En qué orden? El step type explícito es legible; el flag con sub-modos no lo es.

### Alt 2 — Side-channel paralelo (sin step type)

Cuando el runner encuentra ciertos selectores marcados como "security_question" en `bank.yaml`, fuera del flujo de steps, dispara un side-channel similar a OTP.

**Rechazada.** El `map.json` deja de ser source-of-truth completo del flow: parte de la coreografía vive en `bank.yaml`. Esto rompe el contrato declarativo del runner (REQ-004) y complica el linter (necesita validar consistencia entre dos archivos en lugar de uno).

### Alt 3 — Interactive proxy mode (modo "human-in-loop continuo")

Pasar el control del browser al usuario via VNC/noVNC durante el wait y dejar que él complete los pasos manualmente.

**Rechazada.** Re-introduce el problema que open-banca resuelve: navegación manual. Además requiere exponer un puerto adicional del sandbox al usuario, ampliando la superficie de ataque del trust boundary 2 (sandbox).

### Alt 4 — Extender mapper-only handling (solo el Mapper maneja, runner falla)

Que el runner emita `BreakageEvent` cuando encuentre selectores no-mapeados y se delegue al ciclo de remap.

**Rechazada.** Las preguntas de seguridad no son **breakage** — son flujo esperado del banco. Tratarlas como remap dispararía el ciclo de Judge/HITL/Remapper cada login. Costo LLM se dispara, success rate baja, blast radius enorme.

## Open Questions

1. **¿Banco General usa preguntas estáticas (una fija por usuario), rotadas (cambia cada N días), o sampleadas de un pool?** Empíricamente desconocido. Si es **sampleadas**, el modelo de cache-on-first-answer solo funciona parcialmente — habría que pre-cargar N respuestas vía Mapper CLI antes del primer scrape autónomo. La decisión actual asume "estática o pool pequeño" y deja la complejidad de pool-detection para v1.x. **No se ingeniería para sampled-pool en este ADR**; queda como observable durante onboarding.

2. **¿El `question_text` extraído del DOM contiene PII colateral del titular?** Si la pregunta es "¿Calle donde vive su madre?" + el banco renderiza el placeholder con datos parciales del titular, el `question_text` que cruza al cliente vía webhook podría incluir PII. Mitigación parcial: la Capa 1 del PII filter (ADR-0020) actúa sobre `question_text` antes de adjuntarlo al webhook payload. **Pendiente validación empírica** en el primer onboarding real.

## References

- ADR-0019 — BrowserSidecar OTP: [`0019-browser-sidecar-otp.md`](./0019-browser-sidecar-otp.md)
- ADR-0011 — Webhook events HMAC: [`0011-webhook-events-hmac.md`](./0011-webhook-events-hmac.md) (schema HMAC es event-agnóstico, no cambia)
- ADR-0020 — PII redact at LLM boundary: [`0020-pii-redact-llm-boundary.md`](./0020-pii-redact-llm-boundary.md)
- Flow nuevo: [`../03-flows/human-input-pause-resume.md`](../03-flows/human-input-pause-resume.md)
- Componentes afectados: [`../02-components/scraper-runner.md`](../02-components/scraper-runner.md), [`../02-components/orchestrator.md`](../02-components/orchestrator.md), [`../02-components/api.md`](../02-components/api.md), [`../02-components/webhooks.md`](../02-components/webhooks.md), [`../02-components/secrets.md`](../02-components/secrets.md), [`../02-components/mapper-agent.md`](../02-components/mapper-agent.md)
- Threat T27: [`../04-security/threat-model.md`](../04-security/threat-model.md)
- Linter L15: [`../04-security/community-maps.md`](../04-security/community-maps.md)

## Status: Accepted (2026-05-11)
