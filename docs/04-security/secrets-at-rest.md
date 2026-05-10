# Secrets at Rest

## Contexto

Las credenciales bancarias del usuario (user/pass) son el activo más sensible del sistema. Este documento describe la cadena de cifrado, el flujo de uso just-in-time, los puntos de fuga potenciales y los controles. Decisión: [ADR-0008](../adr/0008-sqlcipher-secrets.md).

Cross-refs: [`threat-model.md`](./threat-model.md) (T01-T03) · [`sandbox.md`](./sandbox.md) · [`02-components/secrets.md`](../02-components/secrets.md).

## Cadena de cifrado

| Capa | Algoritmo | Parámetros |
|------|-----------|-----------|
| KDF (master → row key) | Argon2id | mem=256 MiB, iters=3, parallelism=4, salt=16B random per row |
| Cifrado per-row | AES-256-GCM | nonce=12B random per encryption, tag=16B |
| Cifrado del DB | sqlcipher (AES-256-CBC + HMAC-SHA512) | passphrase derivada del master vía Argon2id separado (mem=128 MiB, iters=3) |
| Master passphrase source | env `OPEN_BANCA_MASTER_PASSPHRASE` o prompt TTY al boot | Nunca persistida en disco |

Doble capa: sqlcipher protege el DB completo (defense in depth contra robo de archivo); AES-GCM per-row protege cada credencial con su propia salt+nonce, lo que limita el blast radius si una row leak ocurre por bug en una query.

## Flujo de Store + Retrieve

```mermaid
sequenceDiagram
    participant U as Usuario
    participant API as API
    participant V as Secret Vault
    participant DB as sqlcipher
    participant Orch as Orchestrator
    participant Sbx as Sandbox

    Note over API: Boot: lee master_passphrase de env o prompt
    API->>V: init(master_passphrase)
    V->>V: Argon2id derive db_key
    V->>DB: PRAGMA key con db_key
    Note over V: master_passphrase wipe de memoria luego del init si modo prompt

    U->>API: POST /credentials (TLS)<br/>{bank_id, user, pass}
    API->>V: StoreCredential(bank_id, user, pass)
    V->>V: salt = random(16B)
    V->>V: row_key = Argon2id(master, salt)
    V->>V: nonce = random(12B)
    V->>V: ct = AES-GCM(row_key, nonce, plaintext)
    V->>V: wipe(plaintext, row_key)
    V->>DB: INSERT (cred_id, bank_id, salt, nonce, ct, tag, created_at)
    V-->>API: cred_id
    API-->>U: 201 cred_id

    Note over Orch: Job spawn -- just-in-time decrypt
    Orch->>V: RetrieveCredential(cred_id)
    V->>DB: SELECT row
    V->>V: row_key = Argon2id(master, salt)
    V->>V: plaintext = AES-GCM-decrypt(row_key, nonce, ct, tag)
    V->>V: wipe(row_key)
    V-->>Orch: plaintext blob (in-memory)
    Orch->>Sbx: docker cp blob -> /secrets/cred.json (tmpfs)
    Orch->>Orch: wipe(plaintext blob)
    Sbx->>Sbx: browser-use lee /secrets, registra sensitive_data
    Note over Sbx: LLM ve sólo placeholders. type() resuelve in situ.
    Sbx->>Sbx: container kill -> tmpfs liberado
```

## Parámetros Argon2id

| Parámetro | Per-row | DB key |
|-----------|---------|--------|
| Memory | 256 MiB | 128 MiB |
| Iterations | 3 | 3 |
| Parallelism | 4 | 4 |
| Salt size | 16 B random | 16 B en config (no random; derivado de install) |
| Output size | 32 B (AES-256 key) | 32 B |

Justificación: 256 MiB / 3 iter es competitivo con OWASP cheat sheet 2024 para uso server-side. Cada Retrieve tarda ~300-500 ms en hardware moderno; aceptable porque sólo ocurre al spawn de un sandbox (no por request).

## Rotación de master passphrase

Operación offline batch (requiere stop del API):

1. Operador setea env `OPEN_BANCA_MASTER_PASSPHRASE_NEW`.
2. Comando admin `open-banca rotate-master`:
   - Lee todas las rows.
   - Por cada row: descifra con master viejo, re-cifra con master nuevo (nuevo salt+nonce).
   - Re-cifra DB con sqlcipher `PRAGMA rekey` derivado del master nuevo.
3. Audit log entry `master_rotated` con timestamp y operator id.
4. Stop → swap env var → restart con master nuevo.

Sin downtime real porque el API no acepta tráfico mientras rota; jobs en vuelo abortan con razón explícita.

## Backup y restore

- Backup del archivo SQLite cifrado (`open_banca.db`) es seguro: sin master passphrase es opaco (sqlcipher + Argon2id).
- Estrategia recomendada: snapshot del volumen + copia off-host. No incluir `.env`.
- Restore: copiar archivo + setear env con master passphrase original. Si master se perdió, las creds son irrecuperables y el operador debe re-onboarding.
- Audit log también vive en el DB cifrado.

## Logs, traces y HAR — garantías de no-fuga

Los puntos de fuga potenciales y sus controles:

| Vía | Riesgo | Control |
|-----|--------|---------|
| Langfuse trace de prompts LLM | Cred filtrada al prompt | `sensitive_data` placeholders de browser-use; el LLM ve `<<password>>` no el valor. Test CI con prompt sintético verifica ausencia. |
| OTel span attributes | Cred en `request.body` o atributo custom | Filter middleware con allowlist de span attributes; deny por default todo lo no listado |
| Playwright HAR | Cred en `formData` del POST de login | HAR post-processor: regex redact sobre valores conocidos del job (con cred plaintext en memoria del orchestrator, redact-then-export) |
| Playwright video / screenshots | Password visible si banco lo renderiza | Default: video off en producción, screenshot blur sobre inputs `type=password`; flag debug obligatorio para grabar y firma audit |
| Stdout del container | Cred si el código loguea por error | Stream JSON-only contract; cualquier línea no-JSON es warning + redact attempt antes de OTel |
| Logs del orchestrator | Stack trace con cred en local var | Loguer con redact regex de los valores plaintext mientras viven en memoria |
| Audit log del Vault | Operación + user id, no plaintext | Vault nunca escribe plaintext al audit |
| `.env` con API keys del operador | No es la cred bancaria, pero crítico | File mode 0600, fuera de git, runbook explícito; documentado en T12 |
| Memory dumps / core dumps | Plaintext en heap | `ulimit -c 0` en config recomendada; documentado en runbook |
| Swap | Plaintext en disco | `madvise(MADV_DONTDUMP)` y, en hosts dedicados, swap deshabilitado. Recomendación operativa, no enforced. |

Test CI obligatorios:

- Test que ejecuta un scrape con cred sintética conocida (`SECRET_CANARY_VALUE`) y graba traces Langfuse + OTel + HAR en filesystem; al final, grep por el valor canario debe devolver 0 ocurrencias.
- Test que verifica que el filter middleware tiene allowlist por defecto-deny (no opt-in nuevos campos).

## Memory protection — hard requirements

La passphrase maestra y las derived keys (db_key, row_key) son strings Python en heap sin protección de OS por defecto. Si el proceso es swapeado o produce un core dump, esos bytes aparecen en disco en claro. Esto colapsa la defense-in-depth de sqlcipher + AES-GCM. Las siguientes medidas son **obligatorias** (no recomendadas) para cualquier deployment de producción.

### mlock obligatorio para passphrase y derived keys

La passphrase y toda key derivada de ella deben residir en páginas marcadas con `mlock(2)` para que el kernel no las swapee a disco, y con `madvise(MADV_DONTDUMP)` para excluirlas de core dumps.

Mecanismo de implementación — se elige **opción A como default**; opción B como fallback explícito si la plataforma no soporta la opción A:

- **Opción A (default) — `cryptography` C-extension**: La librería `cryptography` (PyCA) expone buffers de tipo `cryptography.hazmat.primitives.ciphers.aead` y sus tipos de key internos que, cuando se construyen desde el backend OpenSSL, ya residen en buffers C con `mlock` aplicado por OpenSSL en plataformas que lo soportan. El Secret Vault **debe** construir sus keys como objetos `cryptography` nativos y no copiarlos a `str`/`bytes` Python ordinarios más allá del tiempo estrictamente necesario para la KDF.

- **Opción B (fallback) — `mlock(2)` directo via `ctypes`**: Si el framework de la opción A no puede garantizar mlock para un buffer específico (p.ej. salida raw de Argon2id antes de pasarla a AES-GCM), se llama `ctypes.cdll.LoadLibrary(None).mlock(ptr, size)` sobre el buffer `bytearray` y se combina con `madvise(MADV_DONTDUMP)` antes de cualquier uso. El wipe post-uso sobreescribe el buffer con ceros antes de liberarlo (`secrets.token_bytes` length-matched overwrite).

Referencia de amenaza: **CWE-244** — Improper Clearing of Heap Memory Before Release. El heap Python puede dejar residuos de passphrase en páginas que el GC no limpia de inmediato; mlock + wipe explícito es la única defensa confiable.

### swap deshabilitado en host runner

El host (físico o VM) que corre los containers del API debe tener swap desactivado antes de arrancar los servicios:

```bash
# Apagar swap en caliente (host runner, antes de docker compose up):
swapoff -a

# Eliminar swap permanentemente (editar /etc/fstab):
# Comentar o eliminar la línea con 'swap' en /etc/fstab.

# Verificar:
swapon --show   # debe retornar vacío
cat /proc/swaps # debe tener solo la cabecera
```

En entornos cloud (EC2, GCP VM, VPS), asegurarse de que la instancia no tenga swap volume / swap file configurado. El operador debe documentar el estado de swap en su runbook de plataforma.

### core dumps deshabilitados

Core dumps deben estar deshabilitados a dos niveles:

**docker-compose (servicio `api` y `temporal-worker`):**

```yaml
# docker-compose.yml (fragmento normativo):
services:
  api:
    ulimits:
      core:
        soft: 0
        hard: 0
  temporal-worker:
    ulimits:
      core:
        soft: 0
        hard: 0
```

**systemd (si el container corre bajo systemd service):**

```ini
# /etc/systemd/system/open-banca-api.service (fragmento):
[Service]
LimitCORE=0
```

El flag `LimitCORE=0` en systemd y `ulimit -c 0` en el entorno de shell del proceso son equivalentes y deben aplicarse ambos.

Referencia de amenaza: **CWE-528** — Exposure of Core Dump File to an Unauthorized Control Sphere. Un core dump del proceso API en producción contiene el heap completo incluyendo passphrase en claro y todos los row_keys activos en memoria en ese instante.

### boot-time check: API se niega a arrancar si swap activo o swappiness != 0

El API FastAPI debe ejecutar al inicio (antes de aceptar cualquier request, después de leer env vars) una verificación de seguridad de memoria. Si falla, el proceso termina con **exit code 1** y log estructurado de error; no hay override flag (la protección no puede deshabilitarse en producción).

Comportamiento esperado:

```
# Condición de fallo 1:
/proc/sys/vm/swappiness != "0"
→ LOG ERROR: {"event": "startup_security_check_failed", "check": "swappiness", "value": <actual>, "required": 0}
→ sys.exit(1)

# Condición de fallo 2:
/proc/swaps contiene alguna línea más allá de la cabecera (swap activo)
→ LOG ERROR: {"event": "startup_security_check_failed", "check": "swap_active", "devices": [<lista>]}
→ sys.exit(1)
```

En entornos de desarrollo local (donde el operador no puede deshabilitar swap en su máquina), la variable de entorno `OPEN_BANCA_ENV=development` desactiva el check. Esta variable **no puede** tomar el valor `development` en el docker-compose de producción — el CI verifica que el compose de producción tenga `OPEN_BANCA_ENV=production` o la variable ausente.

La verificación es responsabilidad del módulo de arranque del API (`adapters/api/startup_checks.py` o equivalente) y debe tener tests unitarios con filesystem mock.

## Riesgos residuales

- **Operador hostil con master passphrase**: puede descifrar todas las cred. Modelo de amenaza single-org acepta esto explícitamente.
- **Memory de Chromium / orchestrator**: el plaintext existe en RAM minutos. Defensa: tmpfs noexec + container efímero + wipe explícito.
- **Filter middleware regresión**: una span attribute nueva podría no estar redactada. Defensa: test canario obligatorio en CI.
- **Argon2id parámetros desactualizados**: revisión anual en runbook; bump cuando hardware lo permita.
