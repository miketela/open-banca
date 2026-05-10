# Flujo de datos end-to-end — open-banca

Dos diagramas. El primero es el **happy path** completo de un scrape (con OTP pause/resume). El segundo es el **flujo de ruptura** cuando el Scraper detecta que el banco cambió, escala a Judge y al Remapper.

## Happy path — scrape exitoso con OTP

```mermaid
sequenceDiagram
    autonumber
    participant Cli as Cliente
    participant API as API<br/>(FastAPI)
    participant Sec as Secret Store<br/>(sqlcipher)
    participant T as Temporal<br/>Workflow
    participant Sbx as Sandbox<br/>(Docker)
    participant BU as browser-use<br/>Agent (Mapper)
    participant Run as Playwright<br/>Runner
    participant Bank as Banco<br/>(web)
    participant LG as LiteLLM<br/>Gateway
    participant V as Validator
    participant Sto as Storage<br/>(SQLite cifrada)
    participant Maps as Maps Repo
    participant WH as Webhook<br/>Worker

    %% --- 0. Cliente arranca scrape ---
    Cli->>API: POST /scrape {bank, credentials, since}
    API->>Sec: cifra credenciales (Argon2id + AES-GCM)
    Sec-->>API: ok
    API->>Sto: crea Job(status=queued)
    API->>T: start ScrapeWorkflow(job_id)
    API-->>Cli: 202 Accepted {job_id}
    API->>WH: emite job.created
    WH-->>Cli: webhook job.created (HMAC firmado)

    %% --- 1. Workflow arranca el sandbox ---
    T->>Sbx: docker run (network policy: bank + LLM)
    Sbx-->>T: container_id
    T->>Maps: load map.json + parser.json para este banco
    Maps-->>T: artefactos versionados

    %% --- 2. Caso A: existe map.json → Runner ---
    Note over T,BU: Si banco nuevo / sin map → invoke Mapper (browser-use)
    T->>BU: (si aplica) explore + produce map.json
    BU->>LG: completions (Claude Sonnet 4.6 vision)
    LG-->>BU: actions
    BU->>Bank: CDP navigate / click / observe
    Bank-->>BU: screenshots + DOM
    BU-->>Maps: publica map.json + parser.json
    BU-->>T: mapping_done

    %% --- 3. Runner ejecuta el scrape (sin LLM) ---
    T->>Run: execute(map.json, decrypted_credentials)
    Run->>Bank: GET login page (CDP)
    Run->>Sec: lee credenciales descifradas (in-memory)
    Run->>Bank: type username + password
    Bank-->>Run: 2FA prompt (Clave Móvil push)

    %% --- 4. OTP pause / resume ---
    Run-->>T: activity heartbeat (state=waiting_otp)
    T->>WH: emite job.otp_required
    WH-->>Cli: webhook job.otp_required
    Note over T: workflow espera signal (max 4 min)<br/>browser context se mantiene vivo<br/>vía activity heartbeat
    Cli->>API: POST /jobs/{id}/otp-confirmed
    API->>T: signal otp_confirmed
    T->>Run: resume

    %% --- 5. Descarga del Excel ---
    Run->>Bank: navigate a "Estado de cuenta"
    Run->>Bank: aplica filtros (cuentas, rango)
    Run->>Bank: click "Descargar Excel"
    Bank-->>Run: .xlsx (volumen Downloads)

    %% --- 6. Parse Excel con DSL ---
    Run-->>T: download_done(path)
    T->>T: ParseExcel(path, parser.json)
    Note over T: DSL whitelisted:<br/>parse_date, extract_regex,<br/>normalize_amount

    %% --- 7. Validación + dedup ---
    T->>V: validate(rows, balances)
    V->>LG: completions (DeepSeek V3 texto)
    LG-->>V: verdict=ok
    V-->>T: ok
    T->>Sto: dedup + persist transactions + balances

    %% --- 8. Cierre ---
    T->>Sbx: docker rm container
    T->>WH: emite job.completed
    WH-->>Cli: webhook job.completed
    Cli->>API: GET /jobs/{id} (consume resultado)
    API->>Sto: lee transactions + balances
    API-->>Cli: 200 {data}
```

### Notas sobre el happy path

- **OTP pause**: el browser context **NO se cierra** durante la espera. La activity hace heartbeat para indicar a Temporal que sigue viva; Temporal mantiene el activity slot reservado. Si excede 4 minutos, abort + retry desde login (ver [ADR-0015](../adr/0015-no-session-persistence-v1.md)).
- **Credenciales en memoria**: viajan descifradas sólo dentro del sandbox del job, nunca a logs ni a archivos. El sandbox cae al final → memoria liberada.
- **Excel oficial**: el dato canónico es el `.xlsx`, no el DOM. Si la página de transacciones rinde otra cosa (ej. JS lazy load), no nos importa: descargamos el reporte que el banco mismo genera ([ADR-0002](../adr/0002-excel-download-strategy.md)).
- **Webhooks firmados**: cada webhook lleva header `X-OpenBanca-Signature: t=<ts>,v1=<hmac>` ([ADR-0011](../adr/0011-webhook-events-hmac.md)).

## Flujo de ruptura — Scraper detecta cambio → Judge → Remapper

Cuando el banco cambia HTML/flow (selector roto, schema mismatch, redirect inesperado), el Runner produce un error estructurado. El Workflow lo recibe y dispara la cadena de reparación.

```mermaid
sequenceDiagram
    autonumber
    participant T as Temporal<br/>Workflow
    participant Run as Playwright<br/>Runner
    participant Bank as Banco
    participant J as Judge<br/>(DeepSeek V3)
    participant LG as LiteLLM
    participant RM as Remapper<br/>(browser-use + Claude)
    participant Maps as Maps Repo
    participant Sto as Storage
    participant API as API
    participant Cli as Cliente

    %% Detección
    T->>Run: execute(map.json)
    Run->>Bank: click selector "#descargar"
    Bank-->>Run: TimeoutError (selector ausente)
    Run-->>T: error {kind=selector_missing, screenshot, url, step_id}
    T->>Sto: persist Job audit event

    %% Judge clasifica
    T->>J: classify(error_context, recent_runs, map_version)
    J->>LG: completions (DeepSeek V3 texto)
    LG-->>J: structured response
    J-->>T: decision=partial_remap, confidence=0.78, risk=medium

    %% Decisión auto-apply / HITL (regla ADR-0013)
    Note over T: confidence < 0.85 OR risk != low<br/>→ HITL path

    %% Remapper produce el parche (en ambos paths)
    T->>RM: remap(failing_step_id, map.json, screenshots, logs)
    RM->>LG: completions (Claude Sonnet 4.6 vision)
    LG-->>RM: candidate actions
    RM->>Bank: re-explora la sección (CDP)
    Bank-->>RM: nuevo DOM + screenshots
    RM-->>T: RemapProposal {diff, confidence, risk}

    %% HITL: pedir aprobación humana
    T->>API: emit job.remap_proposed
    API->>Cli: webhook job.remap_proposed (diff embebido)
    Cli->>API: POST /remaps/{id}/approve (operador revisó)
    API->>T: signal remap_approved
    T->>Maps: aplica diff → nueva versión map.json
    T->>Sto: audit event RemapApplied

    %% Re-ejecución con map nuevo
    T->>Run: execute(map.json v2)
    Run->>Bank: flujo con selector nuevo
    Bank-->>Run: ok, descarga Excel
    Run-->>T: rows
    T->>API: emit job.completed
    API->>Cli: webhook job.completed

    %% Camino alternativo: rechazo / escalate
    Note over T,Cli: Si Judge devuelve risk=high o<br/>operador rechaza el patch:<br/>job.human_required → operador<br/>arregla map.json a mano
```

### Notas sobre el flujo de ruptura

- **El Runner no intenta "auto-corregir"**: produce un error estructurado y termina. La inteligencia vive en Judge + Remapper, jamás en el path determinístico.
- **Auto-apply vs HITL**: la regla la evalúa el Workflow (use case `ApproveRemap` cuando es manual, lógica directa del workflow cuando es auto). El umbral está en [ADR-0013](../adr/0013-confidence-threshold-remap.md).
- **Re-ejecución**: una vez aplicado el patch, el Workflow re-corre el step desde el principio, no desde el punto de fallo. Más simple, más seguro, idempotencia más fácil.
- **Cap de remap attempts**: 3 por banco por 24h. Si los 3 fallan, circuit breaker `bank_in_repair`; nuevos jobs fallan rápido con error claro.
- **Versionado de maps**: cada apply genera commit en el git del Maps Repo. Rollback = checkout de la versión anterior. Diff humanamente legible.

## Datos en tránsito y at-rest

| Dato | En tránsito | At-rest |
|------|-------------|---------|
| Credenciales bancarias | TLS cliente↔API; descifradas sólo dentro del sandbox | sqlcipher + Argon2id + AES-GCM |
| `.xlsx` descargado | volumen Docker del job (efímero) | retenido configurable, en disco del operador |
| Transacciones canónicas | TLS API↔cliente | SQLite cifrada |
| `map.json` / `parser.json` | filesystem del operador | git versioned, opcional firmado sigstore |
| Eventos webhook | TLS firmado HMAC-SHA256 | log de entregas en SQLite cifrada |
| Prompts/completions LLM | TLS al provider | Langfuse self-hosted (opcional) |

## Referencias cruzadas

- Vista física: [`macro.md`](./macro.md)
- Capas e ports: [`hexagonal.md`](./hexagonal.md)
- Roles de los agentes: [`multi-agent.md`](./multi-agent.md)
- Decisiones: [ADR-0001](../adr/0001-opcion-a-mapper-runner-split.md), [ADR-0002](../adr/0002-excel-download-strategy.md), [ADR-0003](../adr/0003-temporal-orchestration.md), [ADR-0009](../adr/0009-docker-sandbox-per-job.md), [ADR-0011](../adr/0011-webhook-events-hmac.md), [ADR-0013](../adr/0013-confidence-threshold-remap.md), [ADR-0015](../adr/0015-no-session-persistence-v1.md)
