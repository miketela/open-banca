# Arquitectura macro — open-banca

Vista C4 nivel 2 (containers + responsabilidades). Este documento es la entrada principal a la arquitectura. Las vistas finas (hexagonal, multi-agent, data-flow) se derivan de acá.

## Contexto

`open-banca` es una API REST self-hostable que expone, detrás de un contrato simple (`POST /scrape`, webhooks), la captura de información financiera desde bancos panameños que no ofrecen Open Banking. Internamente combina **agentes LLM** (mapping y supervisión), un **runner determinístico** (ejecución repetible) y **Temporal** (durable execution con OTP pause/resume).

Principios macro:

1. **Separación LLM / determinismo** — los LLMs producen artefactos (`map.json`); la ejecución de cada scrape es Playwright puro sin LLM.
2. **Excel-first** — el dato fuente es el reporte oficial del banco; nunca DOM scrape de transacciones.
3. **Sandbox por job** — cada scrape corre en un container Docker efímero con network whitelist al dominio del banco + APIs LLM.
4. **Self-host completo** — sin dependencia obligatoria de servicios cloud propietarios. Langfuse y Temporal opcionales pero auto-hospedables.
5. **AGPL-3.0** — presión institucional intencional.

## Diagrama de containers

```mermaid
flowchart TB
    %% Cliente externo
    Client["Cliente (operador self-host)<br/>POST /scrape, webhooks, polling"]:::ext

    subgraph Edge["Edge / Control plane"]
        API["API Gateway<br/>(FastAPI)<br/>REST + HMAC webhook signer"]:::svc
        Sec["Secret Store<br/>(SQLite + sqlcipher,<br/>Argon2id + AES-GCM)"]:::store
    end

    subgraph Orchestration["Orquestación durable"]
        Temporal["Temporal Server<br/>(workflows + signals)"]:::svc
        Worker["Temporal Worker(s)<br/>activities + heartbeats"]:::svc
    end

    subgraph Agents["Plano de agentes (LLM)"]
        Mapper["Mapper Agent<br/>(browser-use + Claude Sonnet 4.6 vision)"]:::agent
        Remapper["Remapper Agent<br/>(browser-use + Claude Sonnet 4.6 vision)"]:::agent
        Validator["Validator Agent<br/>(PydanticAI + DeepSeek V3)"]:::agent
        Judge["Judge Agent<br/>(PydanticAI + DeepSeek V3)"]:::agent
    end

    subgraph Exec["Plano de ejecución (sin LLM)"]
        Runner["Scraper Runner<br/>(Playwright puro)"]:::svc
        Parser["Excel DSL Engine<br/>(openpyxl + pandas, whitelist)"]:::svc
    end

    subgraph Sandbox["Sandbox per-job"]
        Docker["Docker container efímero<br/>(network policy: banco + LLM)"]:::infra
        Browser["Chromium via CDP<br/>(headless prod / visible debug)"]:::infra
    end

    subgraph Data["Persistencia y artefactos"]
        Maps["Maps Repo<br/>(filesystem + git, sigstore)"]:::store
        Storage["Storage<br/>(SQLite cifrada:<br/>jobs, transactions, balances, audit)"]:::store
        Downloads["Downloads volume<br/>(Excel originales por job)"]:::store
    end

    subgraph Obs["Observabilidad"]
        Lang["Langfuse<br/>(traces LLM, opcional)"]:::obs
        OTel["OTel Collector<br/>(traces/metrics/logs)"]:::obs
    end

    subgraph LLMPlane["Proveedores LLM"]
        LiteLLM["LiteLLM Gateway<br/>(unifica Anthropic + DeepSeek)"]:::svc
        Anthropic["Anthropic API"]:::ext
        DeepSeek["DeepSeek API"]:::ext
    end

    Bank["Web del banco<br/>(Banco General piloto)"]:::ext

    %% Flujos cliente → API
    Client -- "REST + HMAC" --> API
    API -- "webhook firmado HMAC" --> Client

    %% API → secrets + storage
    API -- "lee/escribe" --> Sec
    API -- "lee/escribe" --> Storage

    %% API → Temporal
    API -- "start workflow / signal (gRPC)" --> Temporal
    Temporal -- "schedule activities" --> Worker

    %% Worker invoca planos
    Worker -- "spawn job" --> Docker
    Worker -- "invoca" --> Mapper
    Worker -- "invoca" --> Remapper
    Worker -- "invoca" --> Validator
    Worker -- "invoca" --> Judge
    Worker -- "invoca" --> Runner
    Worker -- "invoca" --> Parser

    %% Sandbox aloja browser
    Docker -- "ejecuta" --> Browser
    Mapper -- "CDP" --> Browser
    Remapper -- "CDP" --> Browser
    Runner -- "CDP" --> Browser
    Browser -- "HTTPS (whitelist)" --> Bank

    %% Mapper produce / Runner consume
    Mapper -- "publica map.json" --> Maps
    Remapper -- "patch / replace map.json" --> Maps
    Runner -- "lee map.json" --> Maps

    %% Excel flow
    Browser -- "descarga .xlsx" --> Downloads
    Parser -- "lee .xlsx" --> Downloads
    Parser -- "rows normalizadas" --> Storage

    %% LLM gateway
    Mapper -- "completions" --> LiteLLM
    Remapper -- "completions" --> LiteLLM
    Validator -- "completions" --> LiteLLM
    Judge -- "completions" --> LiteLLM
    LiteLLM --> Anthropic
    LiteLLM --> DeepSeek

    %% Observabilidad
    Mapper -. trace .-> Lang
    Remapper -. trace .-> Lang
    Validator -. trace .-> Lang
    Judge -. trace .-> Lang
    API -. trace .-> OTel
    Worker -. trace .-> OTel
    Runner -. trace .-> OTel
    Parser -. trace .-> OTel

    classDef svc fill:#1e3a8a,stroke:#1e40af,color:#fff
    classDef agent fill:#7c2d12,stroke:#9a3412,color:#fff
    classDef store fill:#365314,stroke:#3f6212,color:#fff
    classDef obs fill:#581c87,stroke:#6b21a8,color:#fff
    classDef infra fill:#374151,stroke:#4b5563,color:#fff
    classDef ext fill:#0f172a,stroke:#334155,color:#fff
```

## Containers — responsabilidades

### Edge / control plane

- **API Gateway (FastAPI)** — único surface público. Endpoints: `POST /scrape`, `GET /jobs/{id}`, `POST /jobs/{id}/otp-confirmed`, `POST /remaps/{id}/approve`. Firma webhooks con HMAC-SHA256.
- **Secret Store** — credenciales bancarias del cliente cifradas at-rest. KDF Argon2id sobre passphrase maestra del operador, AES-GCM por registro, persistido en SQLite con sqlcipher.

### Orquestación durable

- **Temporal Server** — coordina workflows. Crítico para OTP pause/resume vía `signal` y para retries declarativos.
- **Temporal Worker** — proceso(s) que materializan activities. Una activity = un paso bounded (login, descarga, parse, validate). Heartbeat conserva contexto de browser durante OTP.

### Plano de agentes (LLM)

- **Mapper / Remapper** — agentes que conducen el browser para producir o reparar `map.json`. Construidos sobre la librería `browser-use` (CDP, event bus, tools, sensitive_data injection ya resueltos). Modelo: Claude Sonnet 4.6 con vision.
- **Validator** — inspecciona el resultado parseado del Excel y emite verdicts de sanidad (totales coherentes, fechas dentro de rango, sin nulls inesperados). Modelo: DeepSeek V3 texto.
- **Judge** — invocado cuando Runner o Validator detectan ruptura. Decide entre `retry`, `partial_remap`, `full_remap`, `abort`, `escalate_human`. Modelo: DeepSeek V3 texto.

### Plano de ejecución (sin LLM)

- **Scraper Runner** — interpreta `map.json` con Playwright puro. Determinístico, replay-able, cero llamadas LLM.
- **Excel DSL Engine** — interpreta `parser.json` (DSL whitelisted: `parse_date`, `extract_regex`, `normalize_amount`). Sin Python arbitrario. Encaja con sandbox.

### Sandbox

- **Docker container efímero** — uno por scrape job. Network policy: HTTPS al dominio del banco + endpoints LLM whitelisted (api.anthropic.com, api.deepseek.com); drop everything else. Sin filesystem persistente cross-job; volumen montado para Excel originales.
- **Chromium via CDP** — single-tab browser controlado tanto por `browser-use` (en mapping) como por el Runner (en scrape).

### Persistencia y artefactos

- **Maps Repo** — filesystem + git. Cada `map.json` versionado, diffable, firmado con sigstore/cosign en releases oficiales. `community/` carpeta separada con warning.
- **Storage (SQLite cifrada)** — jobs, transacciones canónicas, balances, audit log, eventos webhook entregados.
- **Downloads volume** — Excel originales del banco por job. TTL configurable.

### Observabilidad

- **Langfuse** (opcional, self-hosted) — traces LLM cross-agent: prompts, completions, costos, latencia.
- **OTel Collector** — traces/metrics/logs del resto del stack (API, Worker, Runner, Parser).

### Plano LLM

- **LiteLLM Gateway** — unifica el cliente. Cambiar de Anthropic a otro proveedor con vision = un cambio de config. Aísla los agentes del SDK del proveedor.

## Protocolos entre containers

| Origen | Destino | Protocolo |
|--------|---------|-----------|
| Cliente | API | REST/HTTPS, JSON |
| API | Cliente | Webhook HTTPS firmado HMAC-SHA256 |
| API | Temporal Server | gRPC (SDK Temporal) |
| Temporal Server | Worker | gRPC (long-poll task queue) |
| Worker | Docker | Docker socket / engine API |
| Mapper / Remapper / Runner | Browser | Chrome DevTools Protocol (CDP) sobre WebSocket local |
| Browser | Banco | HTTPS |
| Agentes | LiteLLM | HTTPS REST |
| LiteLLM | Anthropic / DeepSeek | HTTPS REST |
| Agentes | Langfuse | HTTPS REST async |
| Todo el stack | OTel Collector | OTLP gRPC |
| Worker → API resume | Temporal signal (gRPC) |

## Concurrencia y aislamiento

- Un scrape job = un Temporal workflow = un Docker container = una sesión de browser. No multiplexamos cuentas dentro del mismo container.
- Múltiples jobs simultáneos = múltiples containers paralelos, limitados por recursos del host. El Worker enqueue lo regula.
- El sandbox no comparte cookies, localStorage ni filesystem entre jobs ([ADR-0015](../adr/0015-no-session-persistence-v1.md)).

## Failure domains

- **API caído** → no se aceptan nuevos scrapes; jobs en vuelo siguen porque viven en Temporal.
- **Temporal caído** → workflows pausados, recuperan estado al volver (durable execution).
- **LLM provider down** → Mapper/Remapper fallan con error claro; Scraper Runner sigue corriendo (no depende de LLM); Validator/Judge degradan a reglas básicas (cuando el ADR de fallback se materialice).
- **Banco down / cambia el flujo** → Runner detecta selector roto → Judge → Remapper. Si Remapper falla → escalate human (webhook `job.human_required`).

## Referencias cruzadas

- Layout interno por capas: [`hexagonal.md`](./hexagonal.md)
- Detalle de los 5 agentes: [`multi-agent.md`](./multi-agent.md)
- Flujo end-to-end de un scrape: [`data-flow.md`](./data-flow.md)
- Decisiones que sostienen este diseño: [`../adr/`](../adr/)
- Overview del producto: [`../00-overview.md`](../00-overview.md)
