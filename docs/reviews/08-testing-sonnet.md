# Review de Estrategia de Testing — open-banca

**Revisor**: Claude Sonnet 4.6  
**Fecha**: 2026-05-10  
**Fuentes**: `docs/05-operations/testing.md`, `CLAUDE.md §Testing Framework`, `.taskmaster/docs/prd.txt §REQ-015 + Validation Checkpoints`, `.taskmaster/tasks/tasks.json`

---

## 1. Pirámide actual vs ideal

### Proporciones declaradas

La estrategia declara 70/25/5 (unit/integration/E2E). Para un proyecto con este stack la proporción es correcta en estructura pero la frontera unit/integration está mal dibujada en un punto crítico:

**El tier "Integration (sandbox)"** — spawn de container Docker real con HAR replay — se declara como "cada commit" pero tiene una duración de hasta 10 minutos. Esto viola la regla del CI rápido y va a generar skips frecuentes. En la práctica ese tier debería ser "por PR, job paralelo opt-in por path diff" con una puerta de fallo suave, no "sí bloquea merge". La estrategia lo marca como bloqueante y eso es un problema de sostenibilidad.

### Qué cae en qué nivel — clasificación por scope real

| Componente | Nivel correcto | ¿Cubierto? |
|---|---|---|
| Parser DSL puro | Unit | Si |
| Fingerprint dedup hash | Unit + hypothesis | Si (declarado) |
| Cost counters | Unit | Si (declarado) |
| Judge confidence calibration | Unit con FunctionModel | Si |
| Validator heurísticos | Unit | Si |
| Schema canónico roundtrip | Unit + hypothesis | Parcial (hypothesis no cubre schema discriminator en profundidad) |
| Mapper/Remapper con FunctionModel | Unit (LLM mock) | Si |
| HAR replay scraper runner | Integration | Si |
| Temporal time-skipping completo | Integration | Si |
| Parser vs Excel anonimizado | Integration | Si |
| Sandbox + HAR sin internet | Integration (lento) | Si pero mal colocado en CI |
| Webhook HMAC signature | Unit | **No declarado explícitamente** |
| HMAC clock skew validation | Unit | **No declarado** |
| Cost guardrail abort | Unit | **No declarado** |
| Sandbox escape / network egress | Integration (sandbox) | **No declarado** |
| DLQ / webhook exhaust | Integration (Temporal) | **No declarado** |
| Filter middleware redact | Unit + canary | Si (Task 5, canary en Task 7) |
| Self-healing full loop | Integration (Temporal + HAR) | Parcial — solo Checkpoint 4 manual |

### Gap estructural

El tier Integration (Temporal) cubre `pause_for_otp`, `retry_with_backoff` y circuit breaker por time-skipping, pero no hay un test que combine el loop completo de self-healing: `BreakageEvent → Judge decision (FunctionModel) → Remapper triggered → map patch applied → workflow retried`. Checkpoint 4 es un test manual de demo, no un test automatizado en CI.

---

## 2. HAR record/replay — fixtures y redact pipeline

### Lo que está bien

El pipeline de sanitización está razonablemente especificado:
- Borra `Cookie`, `Authorization`, `X-CSRF-Token`, `setCookie`.
- Reemplaza números de cuenta por hash determinístico.
- Reemplaza nombre del titular por placeholder.
- Anonimiza montos con offset fijo.
- `gitleaks` como gate antes de commit.

### Gaps

**Gap 1 — Sin herramienta de sanitización implementada.** El doc describe los pasos del sanitizer pero no existe ninguna referencia a un script o módulo `har_sanitize.py` en el repo ni en los tasks. Es un proceso manual descrito como si fuera automático. El riesgo es que un maintainer comitee un `raw.har` antes de sanitizar, o que el script sea inconsistente entre maintainers.

Recomendación: Task explícito para `tools/har-sanitizer/` con tests propios y un pre-commit hook que rechace archivos `*.har` que no pasen la verificación de ausencia de patrones secretos (similar al canary test pero sobre ficheros del repo).

**Gap 2 — El sanitizer no cubre response bodies que contengan credenciales embebidas.** Bancos panameños a veces devuelven el nombre del usuario o el número de cuenta en respuestas JSON de dashboard. El pipeline actual borra headers pero no inspecciona ni anonimiza los response bodies de las llamadas JSON. Un HAR de banco que devuelve `{"titular": "JUAN PEREZ", "cuenta": "123456789"}` pasa el pipeline actual sin redactar.

Recomendación: El sanitizer debe aplicar los mismos reemplazos (hash cuenta, placeholder nombre) sobre el contenido de `response.content` para todos los entries de tipo `application/json` y `text/html`.

**Gap 3 — Fixtures cubren happy path y algunos errores HTTP, pero no multi-step failures.** No existe fixture para: OTP expirado a mitad de sesión (no solo "rechazado"), token CSRF rotado mid-session, o descarga de Excel que devuelve 200 pero con contenido de error (patrón común en banca online panameña). Estos son los escenarios más frágiles del scraper.

**Gap 4 — No hay test de fidelidad del HAR replay.** Playwright HAR-based mocking a veces diverge del browser real (timing, redirect handling, cookie scope). No existe un test que compare el comportamiento del scraper contra HAR vs contra banco real en al menos un escenario controlado. Esto solo es posible en el pipeline nightly, pero no está especificado como assertion.

---

## 3. Temporal time-skipping — determinismo con LLM test models

### Lo que funciona

`WorkflowEnvironment.start_time_skipping()` con activities mockeadas es el enfoque correcto. Las activities reciben `StepResult`/`BreakageEvent` predefinidos lo que garantiza determinismo completo porque ningún LLM real es invocado. PydanticAI `FunctionModel` hace posible inyectar respuestas exactas del Judge/Validator.

### Riesgo de determinismo

**El riesgo real no es en el replay, es en la activity registration.** Si un test registra la activity con el mock pero la implementación real registra actividades adicionales (e.g., un side effect de logging o un call a Langfuse), el time-skipping environment puede quedar en estado desincronizado. Temporal tiene un mecanismo de workflow determinism check que puede fallar si el workflow code diverge entre replay e implementación — esto es especialmente peligroso en Python donde los decoradores de Temporal son sensibles al orden de registro.

Recomendación: Los tests de Temporal deben incluir un assertion explícito de que `0 LLM calls externos ocurrieron` (via mock de `litellm.completion` o similar). Checkpoint 2 declara esto ("0 LLM calls en hot path verificado") pero como verificación manual en checkpoint, no como assertion automatizado por test.

### Gap — OTP concurrent signal race

El time-skipping test para `pause_for_otp` cubre timeout de 4 minutos, pero no cubre el race condition donde la señal OTP llega en el mismo tick en que el timeout se dispara. Temporal time-skipping comprime el tiempo pero no garantiza el orden de eventos concurrentes en ese edge. Este es el escenario de mayor riesgo de flakiness en producción y debería tener un test dedicado con ordering explícito.

---

## 4. Canary test SECRET_CANARY_VALUE

### Cobertura actual

Task 5 (Secret vault + filter middleware) define el canary correctamente: inyectar `SECRET_CANARY_VALUE` como password, correr scrape mock, grep recursivo en artefactos. Task 7 (CI pipeline) lo incluye como job `canary` en cada PR.

### Sinks cubiertos vs no cubiertos

| Sink | Cubierto por canary | Observación |
|---|---|---|
| Application logs (Python logging) | Si — grep en artefactos | Depende de que el scrape mock loguee suficiente |
| Langfuse traces | Parcial — si Langfuse está en el mock scope | Langfuse es "opcional via profile". Si el profile no activa Langfuse en CI, el sink no se testa |
| OTel spans | Si — si OTel exporter escribe a disco o stdout en CI | No especificado explícitamente |
| HAR archivos generados | Si — el mock genera HAR y el grep los incluye | Correcto |
| Screenshots de Playwright | **No** — el grep en CI no menciona screenshots explícitamente | Screenshots son archivos binarios, grep no funciona sobre PNG/WebP |
| Error responses de API | **No** — si una excepción embebe el canary value en el 500 response body, no hay test de eso |
| Webhook payloads enviados | **No** — si el webhook delivery incluye context con creds, no hay interceptor de test |
| Temporal workflow history | **No** — Temporal persiste input/output de activities en su DB; si una activity recibe el canary value como param, queda en el history |

### Recomendación crítica

El grep de artefactos sobre archivos binarios (screenshots) no funciona. Se necesita un test específico que extraiga texto de screenshots con `pytesseract` o simplemente que verifique que los screenshots capturados durante el mock run no contengan el `SECRET_CANARY_VALUE` como string en su nombre de archivo ni como metadata EXIF.

Para el sink de Temporal workflow history: el canary test debe incluir una query a la Temporal DB de test (SQLite en dev) y verificar que ningún `activity input`/`output` contiene el valor canario.

---

## 5. Live smoke contra banco real

### Protecciones existentes

- Cuenta de test del operador separada de producción (correcto).
- `RUN_BANK_SMOKE=true` como env var gate, no activo por defecto.
- Nightly + manual trigger — no corre en cada PR.
- Si falla 2 noches seguidas, issue auto-creado.

### Riesgos no mitigados

**Riesgo 1 — Lockout por rate limit.** El nightly corre contra la cuenta de test real. Banco General, como la mayoría de bancos panameños, aplica rate limiting por IP y puede bloquear la cuenta si detecta acceso automatizado nocturno. No existe un mecanismo de cooldown o exponential backoff en el nivel del smoke test — depende de que el banco tolere una sesión por noche. Si el nightly falla y alguien hace `gh workflow run` manual varias veces en el mismo día, el riesgo de lockout aumenta.

Recomendación: Añadir un guard en el smoke test workflow que verifique que el último run exitoso fue hace más de 8 horas antes de proceder. Una semáforo simple via GitHub Actions cache o un GH environment con wait timer.

**Riesgo 2 — OTP en CI sin operador.** El doc menciona `OTP_BOT_WEBHOOK` como opción pero reconoce que bypass de OTP es "no probable" en Banco General. En la práctica, el nightly smoke no puede completarse de forma completamente autónoma sin un humano en el loop para el OTP. Esto convierte el smoke test en un test semi-manual disfrazado de automatizado.

Recomendación: Ser explícito en la documentación que el smoke test de Banco General requiere intervención manual para OTP hasta que se implemente un OTP bot. Marcar el pipeline `smoke-real` como "assisted" y documentar el procedimiento de asistencia.

**Riesgo 3 — Ausencia de assertion sobre número de intentos de login.** El smoke test cubre el happy path pero no verifica que solo realizó 1 intento de autenticación (vs. reintentos que contarían contra el rate limit del banco).

---

## 6. Testing del self-healing flow (Story 3)

### Cobertura actual

El doc de testing provee fixtures PydanticAI para Judge:
- `judge_partial_remap_high_conf` → `decision=partial_remap, confidence=0.9, risk=low` (dispara auto-apply)
- `judge_human_required` → dispara HITL

Y fixture HAR:
- `selector_drift_v1.har` → dispara `partial_remap` en Judge tests

### Gaps críticos

**Gap 1 — No hay test del loop de integración completo.** Los fixtures cubren piezas individuales pero no existe un test que verifique el flujo: `BreakageEvent emitido → Judge decide partial_remap (FunctionModel) → Remapper invocado (FunctionModel) → map patch generado → patch validado → job retried con nuevo map → extracción exitosa`. Checkpoint 4 es un demo manual, no un test automático en CI.

**Gap 2 — Confidence threshold en los bordes.** Los fixtures cubren `confidence=0.9` (auto-apply) y human_required, pero no cubren:
- `confidence=0.85` exactamente (boundary value — ¿inclusivo o exclusivo?)
- `confidence=0.84` (debería ir a HITL)
- `confidence=0.86` (debería auto-apply)
- `risk=medium` con `confidence=0.9` (¿debería bloquear auto-apply?)
- El combinatorio `risk=high` + cualquier confidence

La AC del PRD dice `confidence >= 0.85 AND risk == low` para auto-apply. No hay fixture para `risk=medium/high` con confidence alta.

**Gap 3 — Cap de remap attempts (max 3 por banco por 24h).** Esta regla de negocio no tiene tests declarados en ningún documento. Es un guardrail crítico — si el Judge está en un loop de remap mal calibrado, este cap evita gasto infinito. Debe testearse via Temporal time-skipping con 4 BreakageEvents consecutivos y verificar que el 4to dispara HITL en lugar de otro auto-remap.

**Gap 4 — TTL del proposal (24h).** No hay test con time-skipping que verifique que un proposal expirado no puede ser aprobado via `POST /maps/{bank}/proposals/{id}/approve`.

**Gap 5 — Concurrency de remap.** ¿Qué pasa si dos BreakageEvents llegan simultáneamente para el mismo banco? ¿El segundo espera o genera un segundo proposal? No hay test de concurrencia de self-healing.

---

## 7. Property-based testing con hypothesis

### Cobertura declarada vs aplicación real

El doc declara hypothesis para:
- Parser DSL: shape válida random → nunca explota, nunca produce records con campos faltantes. **Bien.**
- Fingerprint dedup: hash determinístico bajo permutación. **Bien, aunque "único" no es una property de hash — debería ser "colisión rate = 0 sobre corpus representativo".**
- Cost counter: nunca negativo, nunca excede int64. **Bien.**
- Schema discriminator: random payloads válidos roundtrip serialización. **Bien.**

### Gaps en hypothesis

**Gap 1 — HMAC verification no está en la lista.** HMAC-SHA256 para webhooks tiene propiedades que se prestan a hypothesis:
- `verify(sign(payload, key), payload, key)` siempre True (roundtrip).
- `verify(sign(payload, key), payload, wrong_key)` siempre False.
- `verify(tampered_payload, signature, key)` siempre False.
- Robustez ante payloads unicode, payload vacío, payload con bytes nulos.

**Gap 2 — Excel parser fuzz con archivos malformados.** Hypothesis puede generar Excel con shapes inesperadas (celdas fusionadas en posiciones aleatorias, filas vacías intercaladas, tipos de datos mixtos en columna numérica). La declaración actual es solo "shape válida random" — falta el caso de "Excel que parece válido pero tiene datos corruptos" que es el caso real de export de Banco General.

**Gap 3 — Dedup con transacciones idénticas pero IDs diferentes.** El fingerprint dedup debe ser estable a través de re-imports del mismo archivo Excel. Hypothesis debería verificar que `fingerprint(tx_a) == fingerprint(tx_b)` cuando `tx_a` y `tx_b` son la misma transacción económica (mismo monto, fecha, descripción) independientemente del orden de campos o source metadata.

**Gap 4 — Argon2id KDF properties.** El vault usa Argon2id + AES-GCM. Hypothesis podría verificar propiedades de la interfaz: roundtrip encrypt/decrypt con passphrases arbitrarias, que decrypt con passphrase incorrecta siempre falla, que ciphertexts diferentes producen el mismo plaintext solo si la passphrase es la misma.

---

## 8. Gaps de cobertura — priorizado

### Prioridad Critical (bloquea confianza en producción)

**C1 — Canary test no cubre Temporal workflow history como sink.**  
El SECRET_CANARY_VALUE puede quedar persisted en los activity inputs del Temporal history si el vault no redacta antes de pasar params a activities. Requiere: query a Temporal SQLite de test después del mock scrape.

**C2 — Canary test no cubre screenshots (archivos binarios).**  
Grep no inspecciona PNGs. Requiere: test dedicado que verifica que ningún screenshot generado durante el mock run contiene el valor canario (via OCR o via metadata del filename/caption).

**C3 — Self-healing loop integration test no existe en CI.**  
Checkpoint 4 es un test manual de demo. El flujo BreakageEvent → auto-apply no tiene cobertura automatizada. Requiere: test de integración con Temporal time-skipping + FunctionModel Judge/Remapper que verifica el loop completo incluyendo map patch persistence.

**C4 — Confidence/risk boundary values para auto-apply.**  
El threshold `confidence >= 0.85 AND risk == low` no tiene tests en los bordes. Requiere: fixtures para los 6 casos del combinatorio del boundary.

### Prioridad High (afecta seguridad o corrección)

**H1 — Sanitizer HAR no existe como módulo implementado.**  
Es un proceso manual descrito como automatizado. Requiere: `tools/har-sanitizer/` con tests propios y pre-commit hook.

**H2 — HAR sanitizer no cubre response bodies JSON/HTML.**  
Headers redactados pero bodies no. Requiere: extensión del sanitizer con inspección de response content.

**H3 — HMAC webhook verification no tiene hypothesis tests.**  
Propiedades de roundtrip y tamper-detection no verificadas programáticamente.

**H4 — Cap de 3 remap attempts no tiene test.**  
Guardrail crítico sin cobertura. Requiere: test Temporal con 4 BreakageEvents y verificación de que el 4to dispara HITL.

**H5 — TTL de proposals (24h) no tiene test.**  
Requiere: test Temporal time-skipping que intenta aprobar un proposal expirado.

**H6 — Sandbox escape / network egress no tiene test.**  
REQ-018 (docker-socket-proxy hardening) menciona "smoke test endpoints prohibidos" pero no está especificado qué endpoints ni cómo se testa. Requiere: test de integración que spawna el sandbox y verifica que `curl google.com` falla y `curl api.deepseek.com` (o el dominio LLM configurado) funciona.

### Prioridad Medium (calidad y mantenibilidad)

**M1 — OTP concurrent signal race condition no testeado.**  
Race entre señal OTP y timeout en el mismo tick de time-skipping.

**M2 — Live smoke test no tiene guard contra lockout por runs múltiples el mismo día.**

**M3 — Live smoke requiere intervención manual para OTP pero no está documentado como "assisted".**

**M4 — DLQ webhook exhaustion no tiene test.**  
Cuando el endpoint destino del webhook está caído y se agota el retry exponencial, ¿el evento llega a DLQ? ¿El job queda en estado correcto? Requiere test Temporal con activity que siempre falla + verificación de DLQ state.

**M5 — Excel parser fuzz con archivos malformados (hypothesis con shapes inválidas).**

**M6 — Argon2id KDF roundtrip properties con hypothesis.**

**M7 — tasks.json: los tasks no tienen `testStrategy` poblado.**  
El campo existe en la estructura pero los tasks analizados no tienen valor. Esto significa que la estrategia de testing por task está solo en el documento de estrategia general, sin granularidad por task individual.

---

## Resumen ejecutivo

La estrategia está bien diseñada en estructura: pirámide correcta, PydanticAI FunctionModel para LLM isolation, Temporal time-skipping para timing scenarios, HAR replay para browser isolation. El pipeline de CI es razonable.

Los problemas son de **completitud, no de diseño**:

1. El loop de self-healing (el feature core del producto) no tiene test automatizado de integración — solo un checkpoint manual.
2. El canary test tiene 3 sinks sin cobertura (Temporal history, screenshots, webhook payloads).
3. El sanitizer HAR no existe como implementación — es un spec.
4. Los guardrails críticos de negocio (remap cap, proposal TTL) no tienen tests.
5. Sandbox network enforcement no tiene test de egress.

Los gaps C1-C4 deben resolverse antes de cualquier release candidate. H1-H6 deben resolverse antes de Phase 5 (Operations + security). M1-M7 son deuda técnica a resolver antes de v1.0.0.
