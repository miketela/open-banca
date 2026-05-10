# ADR-0013 — Auto-apply de remap si confidence ≥ 0.85 AND risk == low

## Context

Cuando el Scraper Runner detecta una ruptura (`BreakageEvent`), invoca al **Judge** (DeepSeek V3 texto vía PydanticAI) que decide qué hacer (`retry_now`, `partial_remap`, `full_remap`, `abort_and_alert`, `human_required`, etc.). Si la decisión es `partial_remap` o `full_remap`, el siguiente paso es invocar al **Remapper** (Claude Sonnet 4.6 + vision) que produce un nuevo `map.json` y aplicarlo.

Aplicar un remap automáticamente tiene riesgos serios:

- Si el LLM inventa selectores incorrectos, el siguiente intento fallará y consumirá un intento de login (y posiblemente un OTP).
- Múltiples intentos consecutivos fallidos pueden disparar lockout en el banco.
- Un remap propuesto puede tener selectores válidos pero apuntar al flow incorrecto (ej. abrir cuenta de inversión en vez de ahorro).

Pero requerir **siempre** approval humano (HITL) anula el goal de self-healing real: si cada cambio menor de selector requiere intervención, el operador termina actuando como agente manual.

Hay tension entre dos extremos:

- **Full auto** — riesgo de lockout, costo, datos corruptos.
- **Full HITL** — anula self-healing; el operador es bottleneck.

## Decision

**Auto-apply del remap propuesto** sólo cuando el Judge emite simultáneamente:

- `confidence >= 0.85` (0..1, self-reported por el modelo, calibrado por prompt strategy y validators de PydanticAI).
- `risk == "low"` (enum: `low | medium | high`).

En cualquier otro caso (`confidence < 0.85` OR `risk in {medium, high}`) → emit webhook `job.remap_proposed` con bundle de evidencia + endpoint de approval (`POST /jobs/{id}/approve-remap`). El workflow Temporal pausa hasta recibir signal.

Caps independientes que también pueden bloquear auto-apply incluso con confidence/risk OK (ver `cost-guardrails.md`):

- Si quedan ≤1 intentos antes del circuit breaker → forzar HITL.
- Si el cap diario de remaps por banco está alcanzado → abort sin remap.
- Si el cap de costo del job está cerca → abort sin remap.

## Calibración de confidence y risk

Para que la regla `0.85 + low` sea útil, el modelo debe **calibrar bien** confidence/risk. PydanticAI nos da structured output con validators per-field; el prompt strategy enfatiza:

**Para confidence** (alto = el modelo está seguro):

- ¿La causa observada matchea un patrón conocido en el `recent_history` del banco?
- ¿La decisión propuesta tiene precedente exitoso en este banco?
- ¿El fragmento del map afectado es chico y bien delimitado, o es un cambio estructural?
- ¿El Remapper podría correr un dry-run sin enviar creds reales antes del full apply? (Si sí, sumar confidence.)
- Múltiples runs del Judge sobre la misma evidencia → varianza baja = confidence real alto.

**Para risk** (low = la acción es segura):

- ¿La acción consume credenciales adicionales (relogin)? Si sí → no es low.
- ¿La acción requiere OTP del usuario? Si sí → no es low.
- ¿Estamos cerca del cap de remaps del banco/24h? Si sí → no es low.
- ¿El scope del remap es `step_id` (un solo step) o `flow` (todo el download flow)? Sólo `step_id` puede ser low; `flow` es siempre medium o high.
- ¿El flow afectado es el de auth? Auth siempre es high (un error en auth_flow puede locker la credencial).

PydanticAI valida combinaciones imposibles (ej. `decision=full_remap, risk=low` se rechaza y re-prompts).

Adicionalmente, antes de aplicar, el Remapper corre un **dry-run sin creds reales** (creds dummy hasta el primer step de submit), validando que selectores resuelvan. Si el dry-run falla → degrada a HITL automático.

## Consequences

**Positivas**:

- Self-healing real para casos comunes (selector renombrado, copy de botón cambiado): no requiere intervención humana.
- Defensa contra LLM hallucination via dual gate (confidence + risk + dry-run + caps).
- Operador interviene sólo cuando hay genuina ambigüedad.
- Threshold ajustable con override config si la calibración cambia (ej. modelo nuevo, más datos históricos).

**Negativas / costos**:

- Threshold `0.85` es **arbitrario inicialmente**. Va a requerir tuning con datos reales. v1 lo dejamos así con el plan de revisar tras 30 días de operación.
- Auto-apply puede equivocarse en edge cases que el modelo califica como "low risk" pero terminan consumiendo intento de login. Mitigado por circuit breaker y caps.
- Implementación más compleja que cualquier extremo: hay que mantener calibration prompts, validators de PydanticAI, dry-run path, HITL endpoint + webhook, signal Temporal.
- HITL path requiere que el operador esté **conectado y disponible**. Mitigamos: si el approval tarda >24h, el job se cancela con `failure_reason: hitl_timeout` y el operador puede re-disparar.

## Alternatives considered

### Alt 1 — Full auto (siempre aplicar el remap)

**Por qué se rechazó**: riesgo inaceptable de lockout y de aplicar maps incorrectos. Demasiada confianza en el LLM.

### Alt 2 — Full HITL (siempre pedir approval)

**Por qué se rechazó**: anula el goal de self-healing. El operador termina como bottleneck. Para un cliente final que recibe el webhook y aprueba en su teléfono podría ser razonable, pero el remap requiere conocimiento técnico para evaluar (ver el map propuesto, screenshots, diff). No es una acción de cliente final.

### Alt 3 — Threshold sólo en confidence (sin risk)

**Por qué se rechazó**: confidence sin risk es ciego al impacto. Un remap con confidence=0.95 que sin embargo consume relogin + OTP cada vez que se aplica es destructivo aunque "esté seguro".

### Alt 4 — Sólo dry-run, sin LLM scoring

Ejecutar el remap propuesto en dry-run y aplicar si pasa.

**Por qué se rechazó**: dry-run sólo valida que selectores resuelven; no valida semántica (el botón "Descargar" puede haber cambiado de "Excel" a "PDF" — selector resuelve pero descarga lo equivocado). Necesitamos el razonamiento del Judge además del dry-run, no en lugar de.

### Alt 5 — Threshold variable por banco

Cada banco tiene su propio threshold según historial.

**Por qué se rechazó (v1)**: requiere data histórica que no tenemos al inicio. Decisión postergada a v1.x cuando tengamos métricas reales por banco.

## Status

Accepted (2026-05-09).
