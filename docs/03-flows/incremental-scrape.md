# Flow: Incremental Scrape

Corridas siguientes a la primera. Existe `map.json` válido, existe `last_run_cursor` por cuenta, existen transacciones previas en storage. El objetivo es **descargar la ventana mínima necesaria** y mergear contra lo existente sin duplicar.

## Pre-condiciones

- `map.json` versión actual marcado como sano (`circuit_status=ok`).
- Storage tiene `last_run_cursor` por cuenta (timestamp del movimiento más reciente persistido).
- Cliente envía `since=null` (la API resuelve con cursor) o `since=ts` explícito.

## Sequence diagram

```mermaid
sequenceDiagram
    autonumber
    participant C as Cliente
    participant API as FastAPI
    participant W as ScrapeJobWorkflow
    participant Sand as Sandbox Container
    participant Bank as Banco web
    participant ST as Storage

    C->>API: POST /scrape {bank, accounts:[...], full:false, since:null}
    API->>ST: lookup last_run_cursor por cuenta
    API->>W: start workflow {since_per_account}
    API-->>C: 202 {job_id}
    W-->>C: webhook job.created

    %% --- Map ya existe, no hay mapping ---
    W->>W: lookup map.json → v3 ok (skip MapBankWorkflow)

    %% --- Login + OTP ---
    W->>Sand: LoginActivity
    Sand->>Bank: login + Clave Móvil
    Sand-->>W: otp_required
    W-->>C: webhook job.otp_required
    C->>API: POST /jobs/{id}/otp-confirmed
    API->>W: signal otp_confirmed
    W->>Sand: resume login
    Sand-->>W: login complete

    %% --- Loop por cuenta con ventana reducida ---
    loop por cada account
        W->>Sand: NavigateActivity → cuenta
        W->>Sand: select_date_range (from=cursor-buffer, to=today)
        Note over W,Sand: buffer = 3 días (cubre movimientos pendientes de clearing)
        W->>Sand: DownloadExcelActivity
        Sand->>Bank: export ventana corta
        Bank-->>Sand: Excel pequeño
        Sand-->>W: file_ref
        W->>Sand: ParseExcelActivity
        Sand-->>W: payload candidato
    end

    %% --- Dedup MERGE contra storage ---
    W->>ST: lookup transacciones existentes en ventana
    W->>W: dedup 3 niveles (ID embebido → fingerprint → fuzzy transfer)
    Note over W: descarta duplicados, queda delta nuevo
    W->>W: ValidateActivity (sanity sobre el delta)
    W->>ST: insert delta + actualizar last_run_cursor + balances
    W-->>C: webhook job.completed (con count de nuevas tx)
    C->>API: GET /jobs/{id}/result
    API-->>C: 200 payload (sólo nuevas + balances actualizados)
```

## Diferencias clave vs full historical

| Aspecto | Full historical | Incremental |
|---------|-----------------|-------------|
| `map.json` | Lo crea el Mapper si no existe | Reutiliza versión vigente |
| Ventana de descarga | desde fundación de la cuenta | `last_run_cursor - 3 días` hasta `today` |
| Tamaño Excel | grande (años de historia) | pequeño (días/semanas) |
| Costo LLM | Mapper $0.20–$0.40 + Validator | Sólo Validator (~$0.001) |
| Dedup | intra-job (Excel puede duplicar entre meses) | intra-job + **merge contra storage** |
| Tiempo total | 12 – 25 min | 1 – 5 min (dominado por OTP humano) |
| Re-mapping | aplicable si Mapper falla | sólo si runner detecta breakage |

## Buffer de re-descarga (3 días)

Movimientos pendientes de clearing en el banco pueden cambiar fecha o monto entre corridas. Por eso el `since` efectivo retrocede 3 días sobre el `last_run_cursor`. La capa de dedup elimina lo que ya está; lo que cambió (mismo `bank_tx_id` con campos distintos) se trata como **upsert** y registra una entrada en el audit log.

## Dedup: 3 niveles aplicados al merge

```mermaid
flowchart TD
    Tx[Transacción candidata del Excel] --> L1{¿Tiene bank_tx_id embebido?}
    L1 -->|sí| Lookup1[Lookup por bank_tx_id en storage]
    Lookup1 -->|hit| Upsert1[Upsert: actualiza si campos cambiaron]
    Lookup1 -->|miss| Insert1[Insert nuevo]

    L1 -->|no| L2[Calcula fingerprint hash]
    L2 --> Lookup2{¿Match exacto en ventana?}
    Lookup2 -->|hit| Skip[Descarta duplicado]
    Lookup2 -->|miss| L3{¿Es transfer interna entre cuentas?}

    L3 -->|sí| Fuzzy[Fuzzy match: monto+/-tolerance, ventana ±2 días, cuenta contraparte]
    Fuzzy -->|hit| LinkPair[Link transfer pair, marca ambos lados]
    Fuzzy -->|miss| Insert2[Insert nuevo + flag pendiente_pair]

    L3 -->|no| Insert3[Insert nuevo]

    Upsert1 --> Out([Persistir delta])
    Insert1 --> Out
    Skip --> Out
    LinkPair --> Out
    Insert2 --> Out
    Insert3 --> Out
```

Notas:
- `bank_tx_id` cuando existe (Banco General lo embebe en columna Detail) es la fuente de verdad.
- `fingerprint = sha256(account_id|date|amount|description_normalized)` cuando no hay ID.
- Fuzzy transfers requieren ver todas las cuentas del usuario (por eso dedup vive en API, no en cliente).

## Cuándo un incremental se vuelve "más caro"

- El runner detecta breakage en algún step → escala a Judge → posible remap (sube costo).
- Validator detecta anomalía sobre el delta (e.g. monto absurdo, gap de fechas) → `job.human_required`.
- Cliente forzó `full=true` para reconciliación → degenera al flujo full historical.

## Outputs

- Insert/upsert de transacciones nuevas o modificadas.
- Update de `balance` por cuenta y `last_run_cursor`.
- Webhook `job.completed` con resumen `{new_count, upserted_count, skipped_duplicates}`.

## Referencias

- Full historical: [`03-flows/full-historical-scrape.md`](./full-historical-scrape.md)
- Detección de remap: [`03-flows/remap-detection.md`](./remap-detection.md)
- Schema canónico: [`adr/0012-unified-account-schema.md`](../adr/0012-unified-account-schema.md)
