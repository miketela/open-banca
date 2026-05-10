# ADR-0005 — PydanticAI como framework de agentes + LiteLLM como gateway

## Context

`open-banca` necesita dos capacidades distintas en su capa LLM:

1. **Framework de agentes** para Validator y Judge: structured output (Pydantic), tool use opcional, dependency injection limpia, testabilidad. La salida tiene que validar contra schemas estrictos (verdict, confidence score, decision enum).
2. **Gateway unificado a múltiples proveedores**: usamos Anthropic (Claude Sonnet 4.6 vision para Mapper/Remapper) y DeepSeek (V3 texto para Validator/Judge). Posiblemente más en el futuro. No queremos acoplarnos al SDK de un solo proveedor.

Mapper y Remapper se construyen sobre la librería `browser-use` ([ADR-0014](./0014-browser-use-as-mapper-foundation.md)), que tiene su propio acoplamiento a modelos via LiteLLM internamente. El framework de agentes que decidamos no aplica a Mapper/Remapper directamente — aplica a Validator y Judge.

El stack ya es Pydantic-first (FastAPI, schemas canónicos en `shared/schemas/`). Encajar agentes con structured output Pydantic-nativo elimina una capa de fricción.

## Decision

**PydanticAI** como framework de agentes para Validator y Judge.
**LiteLLM** como gateway unificado a proveedores LLM, expuesto vía un único port (`LLMPort`).

Concretamente:

- Validator y Judge se implementan como agentes PydanticAI con prompts versionados y output Pydantic estricto (`ValidationVerdict`, `JudgeDecision`).
- PydanticAI se configura para usar LiteLLM como provider.
- LiteLLM concentra credenciales, routing por modelo, retries de transporte, conteo de tokens y reporting de costos.
- Para Mapper/Remapper, `browser-use` consume LiteLLM directamente (es lo que la librería soporta nativamente).
- El `LLMPort` del dominio expone una interfaz minimal (completion + structured output + cost report). Los adapters (`adapters/llm/litellm`, `adapters/llm/pydanticai`) la implementan.

## Consequences

### Positivas

- **Structured output sin parsing manual**: el output del Validator y Judge es un objeto Pydantic ya validado. Si el modelo devuelve algo malformado, PydanticAI hace retry con feedback al modelo, no nosotros.
- **Type-safety end-to-end**: Pyrefly verifica los flujos `agent → use case → storage`.
- **Testabilidad**: PydanticAI ofrece `TestModel` y `FunctionModel` para tests determinísticos sin pegarle a un LLM real.
- **Provider portability**: cambiar Validator de DeepSeek a otro modelo de texto = cambio de config en LiteLLM. Cero código tocado.
- **Cost observability built-in**: LiteLLM reporta tokens y costos por completion. Sumamos por job para enforcear el cap de $0.50.
- **Múltiples proveedores el primer día**: arrancamos con Anthropic + DeepSeek sin esfuerzo extra por usar dos.

### Negativas

- **Dos dependencias clave** (PydanticAI + LiteLLM) en lugar de una. Si una rompe upstream, hay que mitigarla.
- **PydanticAI es relativamente joven**: API menos estabilizada que LangChain. Mitigación: la usamos detrás del `LLMPort`, así un swap es contenido.
- **LiteLLM es opinable sobre routing**: hay que aprender su modelo de configuración (`model_list`, fallbacks, retry policies). Mitigación: un solo lugar de config, bien documentado.
- **No usamos PydanticAI para Mapper/Remapper**: hay asimetría en el stack. Mitigación: el `LLMPort` lo abstrae; cada adapter usa el framework apropiado para su tarea.

### Operativas

- LiteLLM corre como librería embebida (no como server proxy separado) en v1, para minimizar componentes operativos. Reevaluable si necesitamos rate-limiting cross-job centralizado.
- Tests de Validator/Judge usan `TestModel` con outputs predefinidos; smoke tests con LLM real son opt-in.
- Costos LLM se loggean en Langfuse (cuando opcionalmente activado) y en métricas OTel (`llm_cost_usd_total{agent=...,model=...}`).

## Alternatives Considered

### A — LangChain / LangGraph

**Rechazada.** Razones:

- API ampliamente reportada como inestable y sobre-abstraída para nuestro caso (que es relativamente acotado: 4 agentes, structured output, sin RAG ni memoria conversacional).
- Acoplamiento a su propio modelo de "chains" agrega complejidad que no necesitamos.
- Schemas Pydantic son ciudadanos de segunda; los nuestros son centrales.

### B — Llamar a los SDK de cada proveedor directamente

**Rechazada.** Razones:

- Acoplamiento a vendor lock-in que el proyecto explícitamente quiere evitar (multi-provider desde el día 1).
- Tendríamos que reimplementar cost tracking, retries, routing por nuestra cuenta.
- Dos SDK distintos (Anthropic + DeepSeek) → dos formas de hacer todo.

### C — DSPy

Considerada para Validator/Judge. Rechazada porque su modelo (programar prompts como módulos) es overkill para nuestros agentes pequeños. Reevaluable si terminamos haciendo optimización fina de prompts; por ahora suma complejidad sin beneficio claro.

### D — Instructor (sólo structured output)

Considerada. Rechazada porque sería sólo la mitad de lo que necesitamos: tendríamos structured output pero no framework de agentes (tools, retries con feedback, dependency injection). PydanticAI cubre ambos.

### E — LiteLLM proxy server vs librería embebida

Considerada. Para v1 elegimos la librería embebida en el Worker para minimizar componentes operativos. El proxy server queda como evolución natural si necesitamos rate-limiting global y administración centralizada de keys.

## Status: Accepted (2026-05-09)
