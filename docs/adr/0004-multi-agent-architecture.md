# ADR-0004 — Arquitectura multi-agente: 5 roles separados

## Context

`open-banca` requiere LLMs para varias tareas: explorar bancos nuevos, validar resultados, decidir cómo reaccionar ante rupturas, reparar mapas. Hay dos enfoques:

- **Super-agente único**: un agente con un prompt grande que hace todo (explorar, validar, decidir, reparar). El "framework de agentes" se encarga de routear sub-tareas.
- **Multi-agente con roles separados**: cada función crítica es un agente distinto, con su prompt, su modelo, su presupuesto, sus métricas y su política de retry.

`open-banca` tiene además una particularidad: **el path principal de ejecución (Scraper Runner) es sin LLM** (decisión de [ADR-0001](./0001-opcion-a-mapper-runner-split.md)). Sólo intervienen LLMs en mapping inicial, validación, decisión de reparación y reparación misma. Los agentes son funciones discretas, no un loop continuo.

## Decision

Adoptamos **5 roles separados**, cada uno con prompt, modelo, política y presupuesto independientes:

| Agente | Cuándo corre | Modelo | Output |
|--------|--------------|--------|--------|
| **Mapper** | One-time (banco nuevo) | Claude Sonnet 4.6 vision | `map.json` + `parser.json` |
| **Scraper Runner** | Cada job | **Sin LLM** | Excel + rows |
| **Validator** | Cada job, post-parse | DeepSeek V3 texto | verdict + razones |
| **Judge** | On-break | DeepSeek V3 texto | decision + confidence + risk |
| **Remapper** | Judge ordena reparar | Claude Sonnet 4.6 vision | RemapProposal (diff) |

Cada agente:

- Tiene su propio prompt versionado.
- Reporta a Langfuse con su nombre como `span`.
- Tiene cap de tokens propio.
- Tiene política de retry propia.
- Es invocable independientemente para tests.

## Consequences

### Positivas

- **Cost control granular**: el Validator usa DeepSeek (~10× más barato); el Mapper usa Claude vision (mejor para esa tarea). El gasto se concentra donde aporta y se minimiza donde no.
- **Determinismo del Runner preservado**: el Runner es Playwright puro y no tiene "personalidad LLM" que pueda divergir. Esa propiedad es crítica para auditabilidad y replay.
- **Responsabilidad clara cuando algo falla**: si el Validator marca falsos positivos, ajustamos el prompt del Validator sin tocar Mapper ni Judge. Cada problema tiene un dueño concreto.
- **Métricas separadas**: invocaciones, costo, latencia, tasa de fallo por agente. Trivial detectar regresiones.
- **Independencia evolutiva**: podemos probar cambiar de modelo en un agente sin tocar los otros (ej. Validator → modelo más barato, sin afectar Mapper).
- **Safety boundaries**: el Mapper puede leer DOM del banco; el Validator nunca toca el browser. El Judge no ejecuta acciones; sólo decide. Cada agente tiene la mínima capacidad necesaria.

### Negativas

- **Complejidad de orquestación**: hay que coordinar 5 agentes + sus invocaciones. Mitigación: usamos Temporal ([ADR-0003](./0003-temporal-orchestration.md)), que está hecho para coordinar pasos discretos con retries y signals.
- **Más prompts a mantener**: 5 prompts versionados en lugar de 1. Mitigación: están en repo, son texto, los podemos diff-ear y A/B testear.
- **Más superficie de configuración**: cada agente tiene su modelo, su cap, su política. Mitigación: defaults razonables en config.
- **Risk de cross-agent inconsistency**: dos agentes pueden disagreement (Validator dice ok, Judge dice broken). Mitigación: el Judge es la autoridad final cuando hay conflicto.

### Operativas

- Cada agente tiene su carpeta bajo `adapters/llm/agents/<agent>/` con: prompt, schema de input/output, tests con PydanticAI test models.
- El observability stack distingue agentes en métricas y traces (`agent=mapper|scraper|validator|judge|remapper`).
- Cost guardrails se aplican por agente además de por job.

## Alternatives Considered

### A — Super-agente único con prompt grande

**Rechazada.** Razones:

- Un solo prompt enorme es más difícil de mantener, evaluar y debuggear.
- El modelo único tiene que ser capaz de la tarea más exigente (vision para Mapper) → todo el job paga ese costo.
- Cuando algo sale mal, no hay ownership claro: ¿el problema está en validar, decidir o reparar?
- Los frameworks que prometen "un agente que hace todo" terminan internamente haciendo routing similar; preferimos que el routing sea explícito y nuestro.

### B — 2 agentes (Mapper amplio + Scraper)

Considerada como compromiso. Rechazada porque colapsa Validator/Judge/Remapper en uno solo, que es justo donde el control de costos y de safety boundaries más importa. La cardinalidad 5 es alta pero es la justa.

### C — Sin Validator (sólo Judge reactivo)

Rechazada. Sin Validator no detectaríamos data subtly broken (totales mal, rangos fuera de fecha, columna intercambiada) — sólo errores hard del Runner. El Validator pre-filtra el 95% de los jobs ok, dejando al Judge sólo los casos genuinamente rotos. Sin él, el Judge se invocaría en cada job o no se invocaría nunca.

### D — Mapper y Remapper unificados

Considerada (mismo modelo, mismo `browser-use`). Rechazada porque el contexto es muy distinto: Mapper explora desde cero; Remapper recibe `map.json` viejo + screenshot del fallo + logs y produce un diff. Distintos prompts, distintos presupuestos, distintas métricas. El código compartido vive en el adapter, pero los agentes lógicos son dos.

## Status: Accepted (2026-05-09)
