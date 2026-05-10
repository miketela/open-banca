# ADR-0008-amendment — mlock + swap-off como requisitos duros para la master passphrase

## Status

Amendment to ADR-0008. Accepted (2026-05-10)

## Contexto

ADR-0008 especificó la cadena criptográfica (sqlcipher + Argon2id + AES-GCM) y mencionó como "recomendación operativa" deshabilitar swap y usar `ulimit -c 0`. La revisión de seguridad P0-2 (docs/reviews/02-security-opus.md) identificó que tratar estas medidas como recomendaciones colapsa la defense-in-depth: si el proceso Python es swapeado o produce un core dump, la passphrase maestra y todas las derived keys aparecen en disco en claro, anulando el cifrado at-rest.

Dos CWEs concretos se aplican:

- **CWE-244** (Improper Clearing of Heap Memory Before Release): el heap Python no garantiza limpieza de buffers al liberarlos. Sin mlock + wipe explícito, residuos de passphrase y row_keys quedan accesibles post-uso.
- **CWE-528** (Exposure of Core Dump File to an Unauthorized Control Sphere): un core dump del proceso API contiene el heap completo, incluyendo todos los materiales criptográficos activos en ese instante.

El modelo de amenaza de ADR-0008 defiende robo del DB en frío y acceso lateral. Ambos vectores son alcanzables a través de swap pages y core dumps sin estas protecciones.

## Decisión

Las siguientes medidas pasan de "recomendación" a **requisito duro** para cualquier deployment de producción. Son no-negociables y el API se niega a arrancar si no se cumplen (excepto en `OPEN_BANCA_ENV=development`).

### 1. mlock obligatorio para passphrase y derived keys

La passphrase y toda key derivada (db_key, row_key) deben residir en páginas bloqueadas en RAM:

- **Default (opción A)**: usar objetos nativos de la librería `cryptography` (PyCA). Los buffers de key internos del backend OpenSSL aplican `mlock` en plataformas soportadas. Las keys NO se copian a `str`/`bytes` Python ordinarios salvo el tiempo mínimo necesario.
- **Fallback (opción B)**: cuando el buffer no puede construirse como objeto `cryptography` nativo (p.ej. salida raw de Argon2id antes de pasarla a AES-GCM), aplicar `mlock(2)` vía `ctypes` sobre el `bytearray` y combinar con `madvise(MADV_DONTDUMP)`. El wipe post-uso sobreescribe con ceros antes de liberar.

La razón de elegir opción A como default es que delega la gestión de memoria protegida a una librería auditada (PyCA/OpenSSL) en lugar de código propio en ctypes, reduciendo superficie de error.

### 2. swap deshabilitado en el host runner

El host que corre los containers debe tener swap inactivo antes de `docker compose up`:

```bash
swapoff -a
# Y permanente: eliminar líneas 'swap' de /etc/fstab
```

### 3. core dumps deshabilitados

`ulimits.core = 0` en `docker-compose.yml` para los servicios `api` y `temporal-worker`. Si el proceso corre bajo systemd, también `LimitCORE=0` en el unit file.

### 4. boot-time check en el API

Al arrancar, antes de aceptar requests, el API verifica:

- `/proc/sys/vm/swappiness` == `"0"`.
- `/proc/swaps` no contiene dispositivos de swap activos.

Si alguna condición falla: log estructurado de error con `event: startup_security_check_failed`, luego `sys.exit(1)`. No existe override en producción. En `OPEN_BANCA_ENV=development` el check se omite para facilitar el desarrollo local.

## Alternativas consideradas

### Mantener como recomendaciones pero documentar mejor

Rechazada. Una recomendación no ejecutada silenciosamente es equivalente a no tenerla. El costo del check de boot es mínimo (lectura de dos archivos procfs); el beneficio es convertir un gap de deployment en un fallo detectado en el momento correcto.

### Override flag para producción controlada

Rechazada. Cualquier mecanismo de override se convierte en el vector de ataque. Si hay un entorno legítimo donde swap no puede deshabilitarse (p.ej. servicio cloud con memoria limitada), el operador debe resolverlo a nivel de infraestructura antes de correr open-banca, no deshabilitando la protección.

### mlock sólo con ctypes (sin delegar a `cryptography`)

Rechazada como opción principal. El código propio de mlock en ctypes tiene más superficie de error (tamaño de buffer incorrecto, race condition antes del lock). La opción A (delegar a `cryptography`/OpenSSL) es más segura por defecto.

## Consecuencias

Positivas:

- La defense-in-depth de ADR-0008 es ahora real: el cifrado at-rest no puede ser eludido con un dump de memoria.
- El check de boot convierte errores de configuración de swap en fallos inmediatos y ruidosos, no en vulnerabilidades silenciosas descubiertas en forensics post-incidente.
- CWE-244 y CWE-528 quedan mitigados explícitamente en el diseño.

Negativas / costos:

- El deployment en hosts con swap requiere trabajo adicional del operador para deshabilitarlo.
- En entornos cloud con memoria ajustada, no tener swap puede causar OOM kills en lugar de thrashing. El operador debe sobredimensionar RAM o usar instancias sin swap nativo.
- `mlock` tiene límite por proceso (`RLIMIT_MEMLOCK`). En sistemas con valor bajo del límite, el API falla al lockear buffers. El runbook debe documentar `ulimit -l unlimited` o el equivalente systemd `LimitMEMLOCK=infinity` para el servicio.

Operativos:

- Checklist de boot-time añadido a `docs/05-operations/deployment.md`.
- Detalle de implementación en `docs/04-security/secrets-at-rest.md` §Memory protection — hard requirements.
- Tests unitarios requeridos para el módulo de startup checks (mock de procfs, verificar exit code 1 en condiciones de fallo).

## Referencias

- ADR-0008 original: [`0008-sqlcipher-secrets.md`](./0008-sqlcipher-secrets.md)
- Detalle técnico: [`../04-security/secrets-at-rest.md`](../04-security/secrets-at-rest.md) §Memory protection — hard requirements
- CWE-244: https://cwe.mitre.org/data/definitions/244.html
- CWE-528: https://cwe.mitre.org/data/definitions/528.html
- Master feedback: `docs/reviews/00-MASTER-FEEDBACK.md` P0-2
- Security review: `docs/reviews/02-security-opus.md` P0-2
