# ADR-0008 — sqlcipher + Argon2id + AES-GCM para secrets at rest

## Contexto

open-banca custodia credenciales bancarias del usuario (user/pass). Es el activo más sensible del sistema. Necesitamos cifrado at-rest robusto bajo dos restricciones duras:

- **Single-binary self-host**: `1 docker-compose up` debe traer todo lo necesario. No podemos depender de servicios externos como AWS KMS, GCP KMS o HashiCorp Vault standalone.
- **Operador es admin del sistema**: el modelo de amenaza single-org acepta que el operador tiene acceso al host. Lo que defendemos es robo del DB en frío (backup leak, disco robado) y acceso lateral por procesos no autorizados del mismo host.

## Decisión

Usamos una cadena de tres capas:

1. **sqlcipher** cifra el archivo SQLite completo con AES-256-CBC + HMAC-SHA512. La passphrase del DB se deriva de la master con Argon2id (mem=128 MiB, iters=3).
2. **AES-256-GCM per-row** sobre el campo `credential_blob`. Cada row tiene su propia salt (16B) y nonce (12B) random. La key de AES se deriva de la master + salt con Argon2id (mem=256 MiB, iters=3, parallelism=4).
3. **Master passphrase** vive fuera del DB. Source: env var `OPEN_BANCA_MASTER_PASSPHRASE` o prompt TTY al boot. Nunca persistida.

Doble capa intencional: sqlcipher protege contra robo del archivo; AES-GCM per-row limita blast radius si una query bug expone una row, y permite re-encrypt incremental sin tocar todo el DB (e.g. revoke).

## Alternativas consideradas

### KMS cloud (AWS KMS, GCP KMS, Azure Key Vault)
- **Rechazada**: rompe el principio self-host. Introduce vendor lock-in y obliga al operador a tener cuenta cloud.
- Trade-off perdido: hardware-backed key, audit log nativo, rotation managed.

### HashiCorp Vault o secret manager externo standalone
- **Rechazada para v1**: agrega un servicio más al docker-compose, otro proceso para operar (unseal, audit, backup). Rompe la promesa de "1 binario".
- **Postergada a v2**: si emerge demanda, se puede agregar como adapter opcional manteniendo sqlcipher como default.

### Sólo sqlcipher (sin AES-GCM per-row)
- **Rechazada**: si una query con bug filtra una row al log o al network, el plaintext se leak. Per-row es defense in depth.

### Sólo AES-GCM per-row (sin sqlcipher)
- **Rechazada**: el archivo SQLite plain expone metadata útil al atacante (cantidad de rows, timestamps, IDs de cuenta). Cifrar el archivo entero oculta también la metadata.

### Argon2id parámetros más bajos (mem=64 MiB)
- **Rechazada**: 256 MiB / 3 iter es la recomendación OWASP 2024 para uso server-side. Costo ~300-500 ms aceptable porque Retrieve sólo ocurre al spawn de un sandbox (no por request).

## Consecuencias

Positivas:

- Self-host puro: el binario contiene todo. Operador sólo gestiona el master passphrase.
- Backup del archivo SQLite es seguro: opaco sin la master passphrase.
- Per-row salt+nonce limita el blast radius.
- Argon2id resiste GPU/ASIC mejor que PBKDF2/scrypt.

Negativas / costos:

- Cada Retrieve cuesta ~300-500 ms de CPU + memoria por la KDF. Aceptable porque no es hot path.
- Si master passphrase se pierde, las creds son irrecuperables (no hay recovery key en v1; documentado en runbook).
- Operador hostil con master passphrase puede descifrar todo. Aceptado por modelo single-org.
- Rotación de master requiere stop del API y re-encrypt batch.

Operativos:

- Runbook documenta: file mode 0600 del DB, swap deshabilitado o `madvise`, `ulimit -c 0`, systemd hardening (`PrivateTmp`, `ProtectSystem`).
- Test CI canario: scrape con cred conocida; grep en traces/HAR debe devolver 0.
- Revisión anual de parámetros Argon2id según hardware actual.

Detalle técnico completo: [`04-security/secrets-at-rest.md`](../04-security/secrets-at-rest.md). Componente: [`02-components/secrets.md`](../02-components/secrets.md).

## Status

Accepted (2026-05-09)
