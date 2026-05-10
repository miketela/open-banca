# ADR-0006 — Vision split: Claude para tareas con vision, DeepSeek para texto-only

## Context

`open-banca` tiene 4 agentes LLM activos: Mapper, Remapper, Validator, Judge. Sus necesidades son distintas:

- **Mapper / Remapper** — navegan el browser. Reciben **screenshots** del banco. Necesitan **vision capability fuerte**: identificar campos de login, botones de descarga, tablas, ubicación espacial de elementos. La calidad del razonamiento visual define la calidad del `map.json`.
- **Validator / Judge** — reciben **texto estructurado**: rows parseadas, errores del Runner, logs, descripciones de step. **No necesitan vision**. Su tarea es razonamiento textual: "¿estos totales hacen sentido?", "¿qué tipo de fallo es éste?".

Costos publicados aproximados a la fecha de esta decisión (mayo 2026):

- Claude Sonnet 4.6: ~$3 / 1M input tokens, ~$15 / 1M output tokens. Vision incluida.
- DeepSeek V3: ~$0.27 / 1M input tokens, ~$1.10 / 1M output tokens. Texto fuerte; **vision flojo** (modelo dedicado de vision menos competitivo que Claude).

En tareas de razonamiento textual, DeepSeek V3 es comparable o cercano a frontier. En tareas de vision aplicadas a UIs reales (botones, formularios, tablas dinámicas), Claude Sonnet con vision sigue siendo state-of-the-art.

## Decision

**Asignación de modelos por agente**:

| Agente | Modelo | Capability requerida |
|--------|--------|----------------------|
| Mapper | Claude Sonnet 4.6 (vision) | Vision UI + razonamiento de flujos |
| Remapper | Claude Sonnet 4.6 (vision) | Vision UI + diff de cambios |
| Validator | DeepSeek V3 (texto) | Razonamiento sobre datos tabulares |
| Judge | DeepSeek V3 (texto) | Clasificación + scoring |

Cada modelo se enrutea via LiteLLM ([ADR-0005](./0005-pydanticai-litellm.md)). La asignación vive en config; cambiarla es una edición de config + redeploy del Worker.

## Consequences

### Positivas

- **Cost saving sustancial en steady state**: los agentes que corren en cada job (Validator) o frecuentemente (Judge) usan el modelo barato. Mapper/Remapper, que corren raramente (one-time o on-break), usan el modelo caro pero justificadamente.
- **Calidad alineada con la tarea**: Mapper con Claude vision produce mapas más precisos → menos remaps después. Validator/Judge con DeepSeek mantienen calidad de razonamiento textual a fracción del costo.
- **Margen para cap de $0.50 por job**: con DeepSeek dominando el path frecuente, el cap es realista. Si todo fuera Claude texto, el cap sería más apretado.
- **Provider diversification**: depender de un solo proveedor LLM para todo es riesgo operativo (outage, cambio de pricing, cambio de policy). Tener dos diversifica.

### Negativas

- **Dos cuentas LLM que mantener** (Anthropic + DeepSeek): keys, billing, monitoring de cuotas separadas. Mitigación: LiteLLM concentra la complejidad.
- **Inconsistencia conceptual**: dos "estilos" de LLM en el sistema. Mitigación: cada uno está abstraído por su rol; el desarrollador no piensa "estoy usando DeepSeek", piensa "estoy usando el Validator".
- **DeepSeek es más nuevo en términos de track record operativo que Anthropic**: hay menos historia sobre uptime, rate limits, política de cambios. Mitigación: LiteLLM con fallback config — si DeepSeek devuelve 5xx, hacer retry contra un modelo equivalente en otro provider (cuando el ADR de fallbacks se materialice).
- **DeepSeek pricing/disponibilidad pueden cambiar**: el ahorro depende del precio actual. Mitigación: la asignación es config; revisitable trimestralmente.

### Operativas

- Cap por job de $0.50 aplica al total cross-agente; dentro de eso, DeepSeek aporta pocos centavos y deja headroom para Mapper/Remapper.
- Métricas separadas por modelo: `llm_cost_usd_total{model=...}`, `llm_latency_seconds{model=...}`. Útil para detectar si DeepSeek degrada en latencia o tasa de error.
- Si DeepSeek degrada significativamente en calidad textual, la decisión es revisable agente por agente sin tocar el resto.

## Alternatives Considered

### A — Todo Claude (Sonnet 4.6 vision para todo)

**Rechazada.** Razones:

- Costo: Validator y Judge corren en cada job (Validator) o a menudo (Judge); pagar Claude para texto-puro es desperdicio. Estimado de ~$0.05 por job sólo en Validator → multiplicado por miles de jobs/día = costo dominante evitable.
- Vendor lock-in: depender 100% de Anthropic agrega riesgo operativo.
- No mejora la calidad: Validator/Judge no son tareas donde vision o frontier-text de Claude aporten diferencial.

### B — Todo DeepSeek

**Rechazada.** Razones:

- DeepSeek vision (incluso variantes específicas) es notablemente más débil que Claude vision en UIs reales. Los mapas serían peores → más remaps → más costo en repair.
- Mapper/Remapper corren raramente; el costo extra de Claude está justificado por la mejora en calidad output.

### C — GPT-4 / GPT-5 vision como alternativa para Mapper

Considerada. Rechazada para v1 porque Claude Sonnet 4.6 con vision es nuestro punto de partida y no hay evidencia de que GPT vision sea sustancialmente mejor para esta tarea. Reevaluable; LiteLLM hace que la prueba sea trivial.

### D — Modelo open-source local (Llama 3.x vision, Qwen-VL) para Mapper

Considerada por afinidad al espíritu open-source del proyecto. Rechazada para v1 por: (a) calidad vision aún por detrás de frontier en UIs complejas; (b) infraestructura local de inferencia agrega operación significativa al self-hoster; (c) el cap de $0.50/job ya hace al sistema financieramente viable. Reevaluable a medida que modelos locales mejoren.

### E — Asignación por job, no por agente (router LLM)

Considerada (un router decide qué modelo usar caso por caso). Rechazada como over-engineering. La asignación por agente es predecible, debuggable y suficientemente óptima.

## Status: Accepted (2026-05-09)
