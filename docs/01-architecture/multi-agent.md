# Orquestación multi-agente — open-banca

Seis roles, dos modelos LLM distintos, un runner sin LLM. La separación es intencional: control de costo, determinismo del scrape, responsabilidad clara cuando algo rompe.

## Tabla de agentes

| Agente | Rol | Modelo | Trigger | Output | Cost guardrail |
|--------|-----|--------|---------|--------|----------------|
| **Mapper** | Explora la web del banco y produce `map.json` declarativo de navegación. Descarga el primer Excel. One-time por banco. | Claude Sonnet 4.6 (vision) vía LiteLLM | Banco nuevo o `RemapBank` con `full_remap` | `map.json` candidato, sample Excel | Token budget alto (one-time); cap LLM/job; max 3 remaps/banco/24h |
| **Parser Generator** | Genera `parser.json` (DSL determinista) analizando el sample Excel extraído por el Mapper, o repara un `parser.json` roto ante un `schema_drift`. | Claude Sonnet 4.6 o DeepSeek V3 (texto) | Éxito del Mapper (sample Excel disponible) o decisión `partial_remap (data)` del Judge | `parser.json` validado | Max 3 iteraciones de corrección en loop de dry-run |
| **Scraper Runner** | Ejecuta `map.json` (para navegar) y `parser.json` (para datos) cada corrida. Determinístico, replay-able. | **Sin LLM** (Playwright + Parser Engine) | Cada `POST /scrape` o schedule | Excel descargado + rows parseadas | $0 LLM cost |
| **Validator** | Inspecciona el resultado parseado: totales, fechas, nulls, schema. Emite verdict + razones. | DeepSeek V3 (texto) | Después del parse en cada job | `ValidationVerdict` (ok / suspicious / broken) | DeepSeek ~10× más barato que Claude texto |
| **Judge** | Cuando Runner o Validator detectan ruptura, decide acción. | DeepSeek V3 (texto) | `JobBroken` event, `ValidationFailed` event | Decision: `retry` / `partial_remap` (web/data) / `full_remap` / `abort` / `escalate_human` + `confidence` + `risk` | Texto-only, costo bajo |
| **Remapper** | Reabre el browser, identifica el cambio, propone parche al `map.json`. | Claude Sonnet 4.6 (vision) vía LiteLLM | Judge emite `partial_remap` o `full_remap` | `RemapProposal` (diff + confidence + risk) | Token budget per attempt; cap LLM/job |

## Secuencia high-level — happy path + remap

```mermaid
sequenceDiagram
    autonumber
    %% Participantes en la coreografía multi-agente
    participant Cli as Cliente
    participant API as API (FastAPI)
    participant T as Temporal Workflow
    participant M as Mapper
    participant Maps as Maps Repo
    participant R as Scraper Runner
    participant V as Validator
    participant J as Judge
    participant RM as Remapper

    %% Fase 0: mapeo inicial (one-time por banco)
    Cli->>API: POST /scrape (banco nuevo)
    API->>T: start workflow
    T->>Maps: ¿existe map.json para este banco?
    Maps-->>T: no
    T->>M: invoke Mapper (credenciales + URL banco)
    M->>M: navega, observa, prueba flujos (vision)
    M-->>T: emite map.json + sample_excel.xlsx

    %% Fase 0.5: generación de parser (one-time)
    participant PG as Parser Generator
    T->>PG: invoke Parser Generator (sample_excel.xlsx)
    PG->>PG: analiza Excel, genera candidato parser.json
    PG->>PG: dry-run y corrige errores (max 3 intentos)
    PG-->>Maps: publica map.json y parser.json
    PG-->>T: mapping_done

    %% Fase 1: scrape determinístico (cada corrida)
    T->>R: ejecuta map.json
    R->>R: login, descarga Excel, parse
    R-->>T: rows + balances

    %% Fase 2: validación
    T->>V: validar rows
    V-->>T: verdict=ok
    T-->>API: completed
    API-->>Cli: webhook job.completed

    %% Fase 3 (alternativa): scrape rompe → Judge decide → Remapper
    Note over R,T: --- en otra corrida, banco cambió ---
    T->>R: ejecuta map.json
    R-->>T: error: selector roto o parser falla
    T->>J: classify failure (logs + screenshot/excel)
    
    alt Error de Web (selector roto)
        J-->>T: decision=partial_remap (web), confidence=0.91, risk=low
        T->>RM: invoke Remapper (diff context)
        RM->>RM: navega, identifica nuevo selector (vision)
        RM-->>T: RemapProposal (diff)
    else Error de Datos (schema_drift)
        J-->>T: decision=partial_remap (data), confidence=0.95, risk=low
        T->>PG: invoke Parser Generator (excel fallido)
        PG->>PG: analiza nuevo Excel, repara parser.json
        PG-->>T: RemapProposal (nuevo parser.json)
    end
    
    T->>Maps: apply patch (auto, regla ADR-0013)
    T->>R: re-ejecuta map.json / parser.json
    R-->>T: rows ok
    T->>V: validar
    V-->>T: ok
    T-->>API: completed
    API-->>Cli: webhook job.completed
```

## Diagrama de estados — confidence/risk → auto-apply vs HITL

Regla operativa de [ADR-0013](../adr/0013-confidence-threshold-remap.md): el Remapper produce un parche; el sistema decide si aplicarlo automáticamente o pedir aprobación humana.

```mermaid
stateDiagram-v2
    [*] --> RemapProposed: Remapper emite proposal

    RemapProposed --> Evaluating: Judge anota confidence + risk

    %% Branching por umbrales
    Evaluating --> AutoApply: confidence >= 0.85 AND risk == low
    Evaluating --> HITL: confidence < 0.85 OR risk in {medium, high}
    Evaluating --> Rejected: confidence < 0.5 OR risk == high

    %% Auto-apply
    AutoApply --> Applied: patch escrito en map.json / parser.json
    Applied --> ScrapeRetried: Workflow reanuda con map/parser nuevo

    %% Human-in-the-loop
    HITL --> WaitingApproval: webhook job.remap_proposed enviado
    WaitingApproval --> Applied: operador POST /remaps/{id}/approve
    WaitingApproval --> Rejected: operador rechaza o timeout
    WaitingApproval --> Rejected: timeout (24h sin respuesta)

    %% Escalate
    Rejected --> Escalated: webhook job.human_required
    Escalated --> [*]

    ScrapeRetried --> Validated: Validator ok
    ScrapeRetried --> RemapProposed: vuelve a romper (max 3 intentos / 24h)

    Validated --> [*]
```

## Por qué cada agente

### Mapper — separado del Scraper

Si dejáramos al LLM "scrapear cada corrida", pagaríamos: latencia (segundos por step), costo (~$0.10–$1 por job según volumen) y no-determinismo (mismo banco, mismo flujo, dos resultados distintos). Separar Mapper del Runner permite que el Runner sea Playwright puro: rápido, repetible, debuggable con HAR + video. ([ADR-0001](../adr/0001-opcion-a-mapper-runner-split.md))

### Parser Generator — especializado en datos

Extrae la responsabilidad de comprender formatos de Excel fuera del Mapper. El Mapper lidia con el browser (DOM, vision, login, clicks) mientras que el Parser Generator opera puramente sobre datos tabulares (CSV/Markdown) para emitir el `parser.json`. Esto permite aislar errores, usar modelos de texto más baratos para la etapa de datos, y re-generar parsers si un formato cambia sin tener que correr todo el Mapper visual.

### Validator — pre-filtro barato

DeepSeek V3 texto le pega a "¿estos 142 rows tienen sentido?" mucho más barato que Claude. Filtra el 95% de los casos donde todo está ok antes de involucrar al Judge.

### Judge — decisión pequeña, alto impacto

El Judge no produce contenido ni ejecuta acción; sólo elige una de 5 opciones. Es un classifier con razonamiento. DeepSeek encaja perfecto porque la tarea es texto-puro y el output es enum + score.

### Remapper — separado del Mapper

Mismo modelo (Claude vision), pero distinto **prompt** y distinto **contexto** (recibe el `map.json` viejo, los logs del fallo y screenshots). Operacionalmente es otro agente: presupuesto separado, métricas separadas, política de retry separada. Esa separación importa para observabilidad y cost guardrails.

### Scraper — sin LLM, religiosamente

El runner consume `map.json` y `parser.json`. Cualquier ambigüedad en runtime es un bug del map, no algo que el runner deba resolver "creativamente". Esto hace que: (a) el runner sea testeable con replay HAR sin pegarle al banco; (b) un job en producción tenga costo LLM cero salvo que algo se rompa.

## Cost & risk guardrails

- **Per-job LLM budget**: cap `$0.50` total. Validator+Judge típicamente bajo $0.01; Mapper/Remapper consumen el grueso.
- **Per-bank remap limit**: max 3 intentos por banco por 24h. Excedido → circuit breaker, jobs fallan rápido con `bank_in_repair`.
- **Per-agent token budget**: cada agente recibe un cap de tokens; si lo excede, abort con `budget_exceeded`.
- **Auto-apply gate**: sólo `confidence >= 0.85 AND risk == low`. Cualquier remap dudoso pasa por humano.
- **Login guardrail**: 2 logins fallidos consecutivos → circuit breaker 1h por cuenta.

## Observabilidad por agente

Cada agente reporta a Langfuse con `trace_id = job_id` y `span = agent_name`, permitiendo armar el árbol completo: Mapper → (n × Runner) → Validator → Judge → Remapper. Métricas exportadas por OTel: `agent_invocations_total`, `agent_token_cost_usd`, `agent_latency_seconds`, `agent_failures_total`.

## Referencias cruzadas

- Vista física: [`macro.md`](./macro.md)
- Capas e ports: [`hexagonal.md`](./hexagonal.md)
- Flujo paso a paso: [`data-flow.md`](./data-flow.md)
- Decisiones: [ADR-0001](../adr/0001-opcion-a-mapper-runner-split.md), [ADR-0004](../adr/0004-multi-agent-architecture.md), [ADR-0006](../adr/0006-vision-split-claude-deepseek.md), [ADR-0013](../adr/0013-confidence-threshold-remap.md), [ADR-0014](../adr/0014-browser-use-as-mapper-foundation.md)
