# Banco General Pilot Runbook

> **IMPORTANTE:** `map.json` y `parser.json` en este directorio son **STUBS** (version `0.0.1-stub`).
> Los selectores CSS, nombres de columnas y estructuras son estimados educados.
> **Debes reemplazarlos** con la salida real del Mapper tras el primer run exitoso.
> No ejecutes `POST /scrape` contra el stub en produccion.

---

## Prerequisitos

- Cuenta personal Banco General (no empresarial)
- Credenciales: solo username + password (sin OTP por defecto)
- `ANTHROPIC_API_KEY` configurada para el live Mapper
- Python 3.12+, uv instalado

---

## Paso 0: Pre-checks

Verifica que el entorno este listo antes de continuar.

```bash
# 0.1 Asegurate de tener uv instalado
uv --version

# 0.2 Sincronizar todas las dependencias del workspace
uv sync --all-packages

# 0.3 Instalar Chromium para Playwright (requerido para el Mapper live)
uv run playwright install chromium

# 0.4 Levantar el dev stack (Temporal + dependencias)
docker compose -f docker-compose.dev.yml up -d

# 0.5 Iniciar el Temporal worker (en otra terminal o background)
uv run python -m open_banca_orchestrator.worker &

# 0.6 Iniciar la API (en otra terminal o background)
uv run uvicorn open_banca_api.main:app --reload --port 8000 &

# 0.7 Verificar que la API responde
curl -s http://localhost:8000/health | python3 -m json.tool

# 0.8 Confirmar que ANTHROPIC_API_KEY esta seteada (para el Mapper)
echo "API key length: ${#ANTHROPIC_API_KEY}"
```

**Checkpoint:** Si algun paso falla, revisar la seccion de Troubleshooting al final.

---

## Paso 1: Registrar Credenciales

Guarda tus credenciales de Banco General en el vault cifrado local.

```bash
uv run open-banca register-credentials --bank banco_general --label personal
```

El comando pedira de forma interactiva:
- **Username**: tu usuario de banca en linea BG
- **Password**: tu password
- **PIN**: (requerido para banco_general — deja en blanco si no aplica)

El vault cifra todo con Argon2id + AES-GCM. Ningun valor en texto plano queda en disco.

**Salida esperada:** tabla mostrando los IDs de credenciales almacenadas (sin plaintexts).

---

## Paso 2: Verificar Credenciales

Confirma que las credenciales quedaron almacenadas correctamente.

```bash
uv run open-banca list-credentials --bank banco_general
```

Deberias ver entradas para:
- `personal:username`
- `personal:password`
- `personal:pin` (si se ingreso)

---

## Paso 3: Dry-run del Mapper (sin creds, sin browser)

Antes de ejecutar el Mapper real, verifica que el entorno esta configurado correctamente.

```bash
uv run open-banca run-mapper --bank banco_general --credential personal
```

Sin la variable `OPEN_BANCA_LIVE_MAPPER=1`, el comando imprime el plan de inversion y sale con exit 0.
**No se consume ningun token de LLM, no se lanza ningun browser.**

Salida esperada:
```
DRY-RUN MODE (OPEN_BANCA_LIVE_MAPPER not set)
No browser launched, no LLM calls, no API keys consumed.
...
```

---

## Paso 4: Dry-run del Scraper (valida el stub map.json)

Valida la estructura del `map.json` stub sin lanzar un browser real.

```bash
uv run python scripts/dry_run_scraper.py banco_general
```

Verifica:
- JSON parseable y valido contra el schema `BankMap`
- Todos los `action` son conocidos por el step dispatcher
- Los steps `fill` tienen `value_ref` (no credentials embebidas)
- Orden logico: `navigate` antes de cualquier `fill`
- `ScraperRunner` en modo stub ejecuta sin error

**Salida esperada:** `PASSED — all checks OK`

Si hay errores, el stub tiene un problema estructural — corregir antes de continuar.

---

## Paso 5: Ejecutar el Mapper Live (produce map.json real)

> **ATENCION:** Esto consume tokens LLM (estimado < $0.50 USD) y lanza un browser real.
> Requiere `ANTHROPIC_API_KEY` y conexion a internet con acceso a `www.bgeneral.com`.

```bash
OPEN_BANCA_LIVE_MAPPER=1 uv run open-banca run-mapper \
  --bank banco_general \
  --credential personal
```

El agente:
1. Resuelve credenciales del vault
2. Lanza Chromium headless
3. Navega a `https://www.bgeneral.com/`
4. Explora el login, dashboard, cuentas, selectores de fecha, descarga Excel
5. Genera `map.json` con selectores CSS reales
6. Valida el map con `BankMap.model_validate` + dry-run `ScraperRunner`
7. Escribe el resultado a `packages/banks/banco_general/map.json`

Tiempo estimado: 2-5 minutos. Cost cap: $0.50. Wallclock cap: 5 min.

**Salida esperada:** `map.json written: packages/banks/banco_general/map.json`

Tras este paso, **vuelve a ejecutar el Paso 4** para validar el nuevo map.json real.

### Captura de HAR para fixtures (replay en CI)

Para grabar trafico de red durante el Mapper live y generar un HAR listo para commitear (tras redaccion):

1. Ejecuta el Mapper con captura (ruta raw por defecto: `packages/banks/banco_general/fixtures/har/raw/mapper_run.har`):

```bash
OPEN_BANCA_LIVE_MAPPER=1 uv run open-banca run-mapper \
  --bank banco_general \
  --credential personal \
  --capture-har
```

2. Al terminar con exito, el CLI escribe ademas `packages/banks/banco_general/fixtures/har/sanitized/mapper_run.har` usando `HARSanitizer` (nunca commitees el raw sin revisar).

3. Si necesitas redactar un HAR manualmente (mismo comportamiento que el modulo sanitize):

```bash
uv run python scripts/redact_har.py \
  packages/banks/banco_general/fixtures/har/raw/mapper_run.har \
  packages/banks/banco_general/fixtures/har/sanitized/mapper_run.har
```

Antes de commit: `uv run pytest -m canary` y revisar que no queden secretos en el HAR sanitizado.

---

## Paso 6: Ejecutar Scrape Real via API

Con el `map.json` real generado y validado:

```bash
# Iniciar un job de scrape completo
curl -X POST http://localhost:8000/scrape \
  -H "Content-Type: application/json" \
  -d '{
    "bank": "banco_general",
    "credential_ref": "personal",
    "mode": "full"
  }'
```

Guardar el `job_id` retornado en la respuesta.

---

## Paso 7: Verificar Resultado del Scrape

```bash
# Reemplaza JOB_ID con el id retornado en el Paso 6
JOB_ID="..."

# Polling del estado
curl -s "http://localhost:8000/jobs/${JOB_ID}" | python3 -m json.tool

# Resultado completo (cuando status=completed)
curl -s "http://localhost:8000/jobs/${JOB_ID}/result" | python3 -m json.tool
```

**Estado esperado:** `"status": "completed"` con transacciones en `result.transactions`.

---

## Troubleshooting

### OTP inesperado durante Mapper

Banco General tipicamente no requiere OTP para cuentas personales.
Si el Mapper encuentra un paso de OTP:

1. El agente pausara y emitira un evento `otp_required` en el job.
2. El Mapper intentara documentar el selector del campo OTP en el `map.json`.
3. Para scrapes futuros, el sistema pedira el OTP via webhook/API antes de continuar.

Workaround temporal: ejecutar el Mapper con la sesion iniciada manualmente si el
OTP es requerido para el primer mapping.

### Login rate limit / bloqueo temporal

Banco General puede bloquear temporalmente tras multiples intentos fallidos.

- Esperar 15-30 minutos antes de reintentar.
- Verificar credenciales con `uv run open-banca list-credentials --bank banco_general`.
- Si el problema persiste, revisar si el usuario/password fue cambiado.

### Descarga Excel falla

Si el Mapper no puede descargar el Excel:

1. Verificar que la cuenta tiene movimientos en los ultimos 6 meses.
2. El formato de descarga puede haber cambiado — revisar los logs del Mapper.
3. Inspeccionar el `map.json` generado para los selectores del boton de descarga.
4. Re-ejecutar el Mapper con `OPEN_BANCA_LIVE_MAPPER=1` para redescubrir selectores.

### map.json stub usado accidentalmente en produccion

El stub tiene `version: "0.0.1-stub"`. Si el sistema detecta esta version,
rechaza la ejecucion con un error explicito. Reemplaza siempre el stub con
la salida real del Mapper antes de ejecutar scrapes reales.

### ScraperRunner falla en dry-run

Si `scripts/dry_run_scraper.py` reporta errores:

```bash
# Ver el map.json actual
cat packages/banks/banco_general/map.json

# Verificar el BankMap schema manualmente
uv run python3 -c "
import json
from open_banca_domain.entities.bank_map import BankMap
data = json.load(open('packages/banks/banco_general/map.json'))
bm = BankMap.model_validate(data)
print('OK:', bm.bank_id, bm.version, len(bm.steps), 'steps')
"
```

### Dependencias faltantes

```bash
# Reinstalar todas las dependencias
uv sync --all-packages

# Verificar que open-banca CLI esta disponible
uv run open-banca --help

# Verificar que run-mapper esta registrado
uv run open-banca run-mapper --help
```

---

## Archivos de Referencia

| Archivo | Descripcion |
|---------|-------------|
| `packages/banks/banco_general/map.json` | BankMap stub (reemplazar con salida del Mapper) |
| `packages/banks/banco_general/parser.json` | Parser stub (verificar columnas tras primer scrape) |
| `packages/cli/src/open_banca_cli/commands/run_mapper.py` | Comando `run-mapper` |
| `scripts/dry_run_scraper.py` | Script de validacion del map.json |
| `packages/banks/banco_general/tests/` | Tests del pilot |
| `docs/06-banks/banco-general.md` | Especificacion del banco piloto |

---

## Referencia Rapida de Comandos

```bash
# Setup completo desde cero
uv sync --all-packages
uv run playwright install chromium
docker compose -f docker-compose.dev.yml up -d

# Registrar credenciales
uv run open-banca register-credentials --bank banco_general --label personal

# Verificar credenciales
uv run open-banca list-credentials

# Dry-run Mapper (sin tokens)
uv run open-banca run-mapper --bank banco_general --credential personal

# Validar map.json
uv run python scripts/dry_run_scraper.py banco_general

# Live Mapper (consume tokens LLM)
OPEN_BANCA_LIVE_MAPPER=1 uv run open-banca run-mapper --bank banco_general --credential personal

# Ejecutar tests del pilot
uv run pytest packages/banks/banco_general -v

# Scrape real
curl -X POST http://localhost:8000/scrape -H "Content-Type: application/json" \
  -d '{"bank":"banco_general","credential_ref":"personal","mode":"full"}'
```
