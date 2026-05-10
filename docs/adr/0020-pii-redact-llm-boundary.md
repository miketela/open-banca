# ADR-0020 — Redacción de PII en la frontera LLM (pre-send filter)

## Context

El mecanismo `sensitive_data` de `browser-use` protege credenciales de autenticación (usuario/contraseña) de llegar al LLM provider. Sin embargo, **una vez que el agente completa el login**, el banco renderiza en el DOM y en los screenshots información personal del titular de la cuenta:

- **Nombre completo** del usuario en el header del dashboard (ej. "Bienvenido, Juan Pérez").
- **Números de cuenta** parciales o completos en el listado de cuentas.
- **Saldos numéricos** por cuenta y en el resumen.

Esta información se transmite en dos canales distintos al LLM provider:

1. **Screenshots (imágenes)** — enviados a Claude Sonnet 4.6 (Anthropic US) como parte del loop de vision de Mapper y Remapper (ver [ADR-0006](./0006-vision-split-claude-deepseek.md)).
2. **DOM textual extraído** — `extract_dom()` devuelve fragmentos del árbol DOM que pueden incluir texto renderizado con nombres y números de cuenta; este texto alcanza a todos los agentes con LLM: Mapper, Validator, Judge y Remapper.

Los proveedores actuales del sistema son Anthropic (US) y DeepSeek (China). La **Ley 81 de 2019 de Panamá, Art. 13** establece que la transferencia internacional de datos personales requiere consentimiento explícito del titular o alguna de las bases habilitantes tipificadas. En el modelo de v1 (self-hosted, single-org) el operador actúa por cuenta propia, pero los datos procesados corresponden al titular de la cuenta bancaria, cuyo consentimiento no está actualmente capturado para transferencias a proveedores LLM externos.

La amenaza está documentada como **T18** y **T26** en el threat model actualizado (ver [`../04-security/threat-model.md`](../04-security/threat-model.md)).

La nota en T15 ("Proveedor LLM ve screenshots del banco logueado — inevitable, documentado") ya reconocía la superficie pero la trataba como residual aceptado. Este ADR cambia esa postura a **mitigado activamente**.

## Decision

**Implementar un filter middleware de redacción de PII que actúa inmediatamente antes de enviar cualquier payload al LLM provider vía LiteLLM.**

El filtro opera en dos capas según el canal:

### Capa 1 — Redacción en texto / DOM (todos los agentes: Mapper, Validator, Judge, Remapper)

Un interceptor en la capa LiteLLM (`adapters/llm/pii_filter.py`) transforma el texto de los mensajes antes del envío:

| Tipo de PII | Mecanismo | Resultado |
|-------------|-----------|-----------|
| Número de cuenta bancaria PA | Regex: `\b\d{3,4}-\d{4,6}-\d{1,2}\b` y variantes sin guiones | `[ACCT-XXXX]` (retiene últimos 4 dígitos para traceabilidad de agente) |
| Nombre en header conocido | Selector configurado en `map.json` con etiqueta `"pii": true` (campo `user_header`) | `[NOMBRE-REDACTED]` |
| Saldo numérico | Regex: valores monetarios `\$?\d{1,3}(,\d{3})*(\.\d{2})?` en contexto de saldo (heurístico + selector `"pii": "balance"`) | Reemplazado por orden de magnitud: `[BAL~100K]`, `[BAL~10K]`, etc. |
| Correo electrónico | RFC 5321 local-part + dominio | `[EMAIL-REDACTED]` |
| Cédula / pasaporte PA | `\b\d-\d{3,4}-\d{3,4}\b` | `[ID-REDACTED]` |

La sustitución de saldo **preserva el orden de magnitud** (`~10`, `~100`, `~1K`, `~10K`, `~100K`, `~1M`) para que el agente pueda razonar sobre coherencia relativa sin acceder al valor exacto.

Los selectores marcados como `"pii": true` o `"pii": "balance"` en `map.json` son definidos por el banco en su proceso de onboarding. Si el selector no está marcado, se aplica únicamente el regex de fallback.

### Capa 2 — Redacción en imagen / screenshots (sólo Mapper y Remapper — agentes con vision)

Un preprocessor de imagen (`adapters/llm/screenshot_redact.py`) actúa sobre cada imagen antes de adjuntarla al mensaje:

- Lee las coordenadas de las regiones marcadas como `"pii": true` del `map.json` (campo `pii_regions[]` por paso del flow).
- Aplica un bloque sólido (fill negro) sobre cada región.
- Regiones no definidas en el map → no se redactan en la imagen (el texto DOM del mismo área sí cae bajo Capa 1).
- Durante el **onboarding** (primer Mapper run, cuando el map aún no existe), la Capa 2 no puede actuar. Se documenta como gap de onboarding: el primer run transfiere más PII que los runs subsiguientes. Mitigación parcial: el objective prompt instruye al agente a evitar extraer DOM de zonas de header.

### Configuración del filter

```
# map.json — fragmento de ejemplo
{
  "pii_regions": [
    { "step": "dashboard", "selector": ".user-greeting", "pii": true },
    { "step": "account_list", "selector": ".account-number", "pii": true },
    { "step": "account_list", "selector": ".account-balance", "pii": "balance" }
  ]
}
```

El filter se activa en todos los agentes por defecto. Puede desactivarse por agente con flag `pii_filter_enabled: false` en la configuración del agente (no recomendado; requiere justificación en el audit log).

### Logging y trazabilidad

- Los valores originales **nunca** se escriben en el log. El log registra `pii_redacted=true` y el conteo de sustituciones por tipo por turn.
- Los traces de Langfuse reciben los mensajes ya redactados (el filtro actúa antes de que LiteLLM genere el span).
- Un CI test obligatorio verifica que un prompt sintético con PII canaria no aparezca en el payload real enviado al provider (pattern similar al canary test de credenciales en T03).

## Alternatives Considered

### A — Restricción de provider a región geográfica (Anthropic EU only)

Usar endpoint EU de Anthropic para eliminar transferencia fuera del EEE, más permisivo bajo GDPR/Ley 81.

**Rechazada.** DeepSeek (usado por Validator y Judge) no ofrece endpoint EU en v1. Esta opción requiere migrar Validator/Judge a otro modelo con endpoint EU, aumentando costo y complejidad. Además, no resuelve el problema de datos en tránsito hacia el provider; sólo cambia la jurisdicción del storage.

### B — LLM self-hosted (Qwen-VL, Llama 3.x vision)

Correr el LLM en la misma infraestructura del operador. Ninguna transferencia transfronteriza.

**Rechazada para v1.** Requiere GPU dedicada, mantenimiento de modelo, y calidad inferior para tareas de vision bancaria. Documentado como opción v2 en [ADR-0006](./0006-vision-split-claude-deepseek.md). El ADR-0006 ya prevé este camino; ADR-0020 no lo cierra — lo complementa hasta que v2 lo materialice.

### C — Status quo + disclosure y consentimiento explícito al usuario final

Mantener el flujo actual y exigir que el operador obtenga consentimiento del titular de la cuenta antes de usar la API.

**Rechazada.** Implica que la plataforma, por diseño, transfiere PII sin control técnico. El consentimiento puede revocarse o no obtenerse; el sistema no puede verificarlo. Privacy by design (Ley 81 Art. 4) exige controles técnicos, no solo contractuales.

### D — Redacción solo en logs (no en payload LLM)

Ampliar el filter de T03 (creds en logs) para cubrir PII financiera en Langfuse/OTel, pero enviar el payload completo al LLM.

**Rechazada.** No mitiga la transferencia transfronteriza al provider. El problema es el payload hacia Anthropic/DeepSeek, no los logs internos.

## Consequences

### Positivas

- **Compliance con Ley 81 PA Art. 13**: el sistema puede argumentar que transfiere al LLM provider datos anonimizados/seudonimizados, no datos personales identificables. Reduce la exposición legal del operador y de los usuarios finales.
- **Defensa en profundidad**: suma una capa al modelo de amenazas. T15, T18, T26 pasan de "residual aceptado" a "mitigado activo".
- **Aplicable inmediatamente a los proveedores actuales** (Anthropic US, DeepSeek CN) sin cambio de provider.
- **Compatible con ADR-0006**: la decisión de split Claude/DeepSeek no se altera; el filter actúa transparentemente en la capa LiteLLM antes del routing.

### Negativas

- **Overhead de latencia**: el filter textual es O(n) sobre el tamaño del mensaje; estimado <5 ms por turn con regex precompilado. La redacción de imagen es O(px) pero opera sobre screenshots ya capturados; estimado <50 ms. Aceptable dado el presupuesto de wallclock_timeout del Mapper (8 min).
- **Riesgo de sobre-redacción**: si el regex es demasiado agresivo, puede redactar selectores o identificadores de pasos que el agente necesita para razonar (ej. un paso ID que incluye dígitos tipo `step_3456`). Mitigación: lista de exclusiones (`pii_filter_exclusions`) en la configuración del agente.
- **Gap en onboarding (primer Mapper run)**: Capa 2 (imagen) no puede actuar antes de que el map defina `pii_regions`. El primer run transfiere más PII que los subsiguientes. Documentado; aceptado como trade-off inevitable para bootstrapping.
- **Calidad del Mapper si sobre-redacta**: si el agente recibe balances como `[BAL~10K]` en vez del valor exacto, puede tomar decisiones subóptimas en pasos que requieren comparar montos. Mitigación: el agente sólo usa balances para verificar coherencia relativa, no para cómputos exactos (esa responsabilidad es del Scraper Runner sobre el Excel).

### Operativas

- El campo `pii_regions[]` en `map.json` es obligatorio para bancos nuevos a partir de la versión que implemente ADR-0020. Maps anteriores sin el campo se tratan con filter regex-only (Capa 1 sin selectores, Capa 2 desactivada).
- El CI canary test de PII se agrega al mismo pipeline que el canary test de credenciales (T02/T03), con distintas señales: `PII_CANARY_NAME`, `PII_CANARY_ACCT`, `PII_CANARY_BAL`.
- Los docs de `mapper-agent.md`, `judge-agent.md`, `validator-agent.md` y `remapper-agent.md` referencian este ADR en su sección de seguridad.
- El ADR-0006 (vision split) y T15 del threat model se actualizan para reflejar que el residual "screenshots del banco logueado" está ahora mitigado por ADR-0020.

## Status: Accepted (2026-05-10)
