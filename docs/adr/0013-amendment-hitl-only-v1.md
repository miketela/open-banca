# ADR-0013 Amendment — v1 HITL-Only: Defer Auto-Apply to v1.x

**Status**: Amendment to ADR-0013. Accepted (2026-05-10). Supersedes auto-apply path until v1.x.

---

## Context

ADR-0013 estableció que si el Remapper devuelve `confidence ≥ 0.85 AND risk == low`, el `RemapBankWorkflow` aplica el patch automáticamente al `map.json` sin intervención humana.

Esta enmienda identifica dos defectos estructurales en ese diseño que lo hacen inseguro para el piloto Banco General:

**1. El threshold `0.85` es arbitrario sin baseline calibrada.**

La propia ADR-0013 admite en sus Consequences que `Threshold 0.85 es arbitrario inicialmente`. No existe data histórica de corridas reales sobre Banco General. El threshold fue elegido como estimación de ingeniería, no derivado de precision/recall observados. Activar auto-apply sobre un threshold no calibrado es equivalente a no tener guardrail.

**2. Loop circular de visión: el modelo que vota no vio la pantalla.**

El flujo de decisión tiene una asimetría arquitectural:

- El **Judge** (DeepSeek V3 texto) decide si la confianza es suficiente para auto-aplicar.
- El **Remapper** (Claude vision) es el único agente que realmente exploró el banco y vio los screenshots.
- Judge recibe el screenshot como `dom_snapshot_summary` (texto OCR/heurístico), no como imagen.

El modelo que toma la decisión de auto-apply (`confidence ≥ 0.85`) razona sobre evidencia OCR degradada del estado visual del banco. El modelo que vio la pantalla (Remapper Claude vision) no tiene voto. Esta asimetría introduce ruido sistemático en la evaluación del confidence del Judge sobre decisiones de remap.

**3. Consecuencias operativas en piloto.**

Un auto-apply incorrecto durante el piloto Banco General modifica el `map.json` en producción. Si el patch es erróneo, el siguiente scrape falla con una causa diferente (selector nuevo aplicado en el lugar equivocado), el circuit breaker puede abrirse, y el operador queda bloqueado hasta diagnóstico manual. El blast radius de un solo auto-apply incorrecto en piloto es mayor que el costo de requerir aprobación manual durante la fase de calibración.

---

## Decision

**v1 elimina la rama auto-apply en su totalidad.**

Todos los breakages que requieren remap (`partial_remap` o `full_remap`) siguen el camino HITL sin excepción:

1. Remapper propone el patch.
2. `RemapBankWorkflow` persiste el proposal con `status=pending`.
3. Se emite webhook `job.remap_proposed` al operador con diff, evidence, confidence, risk, y `expires_at`.
4. El workflow pausa esperando signal `remap_approved | remap_rejected | timeout 24h`.
5. El operador aprueba o rechaza vía `POST /maps/{bank}/proposals/{id}/approve`.

El schema de `confidence` y `risk` del Judge y del Remapper **se mantiene intacto** con propósito de telemetría. Durante los primeros 90 días de operación en piloto se recopila la distribución real de `(confidence, risk, approved_by_human)` para construir la baseline de calibración.

**Condición de reactivación de auto-apply (v1.x):**

Auto-apply se puede reactivar en v1.x únicamente si la data empírica muestra:

- Precision de auto-apply ≥ 0.95 medida como `remap_auto_applied_correct / remap_auto_applied_total` sobre al menos 50 eventos reales.
- El threshold candidato se deriva de la distribución observada (no elegido a priori).
- La reactivación requiere ADR separado con los datos como evidencia.

---

## Consequences

**Positivas:**

- Elimina el "loop circular" de visión: ninguna decisión de modificar el `map.json` ocurre sin revisión humana durante el piloto.
- Permite recopilar baseline empírico real de precision/recall del par (Judge + Remapper) sobre Banco General.
- Protege al operador en piloto: un remap incorrecto requiere 1 click para ser rechazado en lugar de requerir diagnóstico post-facto de un `map.json` mutado incorrectamente.
- Simplifica el código de `RemapBankWorkflow` en v1: no hay rama condicional de auto-apply, siempre se persiste el proposal y se pausa.

**Negativas / costos:**

- El operador debe aprobar cada remap: costo de 1 webhook + 1 click por incidente de breakage que requiere remap.
- En alta frecuencia de breakages (ej. Banco General rediseña en horario pico), el operador puede recibir múltiples proposals en paralelo. Mitigado por el cap de `remaps_used_24h ≥ 3 → human_required` (ya existente) que consolida la señal.
- Goal 2 del PRD ("self-healing target ≥ 70% auto-remap exitoso") se redefine para v1: el target pasa a ser "≥ 70% de breakages que requieren remap se resuelven con 1 sólo HITL approval (sin escalar a multi-touch o cancelación por timeout)". Ver actualización en prd.txt REQ-005.
- La infraestructura de confidence/risk en Judge y Remapper debe mantenerse activa (no se puede simplificar) porque es el instrumento de medición para la baseline.

---

## Alternatives Considered

### Alt A — Judge-vision (Claude) en breakage path

Reemplazar DeepSeek V3 en Judge por Claude Sonnet con vision, permitiendo que el modelo que vota sí vea los screenshots. Esto cerraría el loop circular.

**Rechazada:** cada evento de breakage dispara un Judge call. Con Claude vision el costo por breakage sube ~$0.05. En un banco que rompe 5 veces al día durante piloto, eso es $0.25/día solo en Judge — antes del costo del Remapper. Además introduce latencia. El loop circular es un defecto arquitectural, pero la solución correcta es HITL en v1, no aumentar el costo por evento.

### Alt B — Retener auto-apply con threshold más alto (0.95)

Subir el threshold de 0.85 a 0.95 para reducir el espacio de auto-apply a casos de altísima confianza.

**Rechazada:** sin data calibrada, 0.95 es tan arbitrario como 0.85. El Judge puede auto-reportar confidence=0.97 en un caso que resulte erróneo porque el modelo nunca vio la pantalla. El problema no es el número del threshold, es la ausencia de baseline empírica para validarlo. Fijar un número más alto da una falsa sensación de seguridad.

---

## References

- ADR original: [`0013-confidence-threshold-remap.md`](./0013-confidence-threshold-remap.md)
- Flujo afectado: [`../03-flows/remap-approval.md`](../03-flows/remap-approval.md)
- Componente afectado: [`../02-components/judge-agent.md`](../02-components/judge-agent.md)
- Reviews que motivaron esta enmienda: `docs/reviews/01-architecture-opus.md` RC2, `docs/reviews/06-banco-general-sonnet.md` BLK-3
