# Pipeline de CI/CD

## Visión general

El pipeline de CI se activa en cada pull request y en pushes a `develop` y `main`. Consta de tres jobs independientes que se ejecutan en paralelo tras el job principal de lint/test:

```
lint-type-test
    ├── canary-gate          (depende de lint-type-test)
    └── docker-compose-validate  (depende de lint-type-test)
```

## Workflows

### `ci.yml` — Pipeline principal

**Triggers:** `pull_request` hacia `develop`/`main` y `push` a `develop`/`main`.

#### Job: `lint-type-test`

| Paso | Comando | Falla si... |
|------|---------|-------------|
| ruff check | `uv run ruff check .` | errores de linting |
| ruff format | `uv run ruff format --check .` | formato inconsistente |
| pyrefly | `uv run pyrefly check` | errores de tipos (strict mode) |
| pytest (main suite) | `uv run pytest -m "not live and not canary" -q` | cualquier test falla |

Los tests marcados `live` (requieren credenciales bancarias reales) y `canary` (gate de seguridad separado) quedan excluidos de este job.

#### Job: `canary-gate`

Ejecuta exclusivamente los tests marcados `@pytest.mark.canary`. Estos tests verifican que `SECRET_CANARY_VALUE`, `PII_CANARY_NAME`, `PII_CANARY_ACCT` y `PII_CANARY_BAL` nunca aparecen en logs, spans OTel ni streams Langfuse (REQ-016).

Si cualquier canary falla, CI falla por completo — no hay merge posible hasta que el filtro redact sea corregido.

Corre localmente con:

```bash
uv run pytest -m canary -v
```

#### Job: `docker-compose-validate`

Valida que los archivos de Docker Compose tienen sintaxis correcta sin levantar contenedores:

```bash
docker compose -f docker-compose.dev.yml config --quiet
docker compose -f docker-compose.yml config --quiet
```

### `security-scan.yml` — Escaneo de seguridad

**Triggers:** `pull_request` hacia `develop`/`main` + schedule semanal (lunes 06:00 UTC).

| Herramienta | Scope | Falla si... |
|-------------|-------|-------------|
| `pip-audit` | dependencias Python | vulnerabilidades HIGH o CRITICAL |
| `trivy fs` | filesystem completo | HIGH o CRITICAL encontrados |
| `gitleaks` | historial git completo | secretos detectados |

Solo usa `GITHUB_TOKEN` estándar; no se requieren secrets adicionales salvo `GITLEAKS_LICENSE` (opcional para repos privados).

## Dependabot (`.github/dependabot.yml`)

| Ecosystem | Directorio | Frecuencia |
|-----------|-----------|-----------|
| `pip` (uv workspace) | `/` | semanal (lunes) |
| `github-actions` | `/` | semanal (lunes) |
| `docker` | `/` | mensual |

## Pre-commit hooks (`.pre-commit-config.yaml`)

Instalar una vez:

```bash
uv run pre-commit install
```

Ejecutar manualmente sobre todos los archivos:

```bash
uv run pre-commit run --all-files
```

| Hook | Descripción |
|------|-------------|
| `ruff` | Lint + auto-fix |
| `ruff-format` | Formato consistente |
| `pyrefly-check` | Type-check strict |
| `har-sanitizer-gate` | Bloquea HAR con credenciales sin redactar (Task 11) |
| `check-merge-conflict` | Detecta markers de conflicto |
| `check-added-large-files` | Bloquea archivos >1 MB |
| `check-yaml` | Valida YAML |
| `detect-secrets` | Detecta secretos contra baseline `.secrets.baseline` |

### Actualizar el baseline de detect-secrets

Si se añaden nuevos archivos con patrones que no son secretos reales (falsos positivos):

```bash
uv run detect-secrets scan --exclude-files 'uv\.lock' --exclude-files '\.secrets\.baseline' \
  --exclude-files 'package-lock\.json' > .secrets.baseline
git add .secrets.baseline
git commit -m "chore(security): update detect-secrets baseline"
```

## Ejecutar el canary localmente

El canary gate reproduce exactamente lo que corre en CI:

```bash
# Solo tests canary (gate de seguridad REQ-016)
uv run pytest -m canary -v

# Suite completa sin live ni canary (igual que CI job lint-type-test)
uv run pytest -m "not live and not canary" -q

# Suite completa sin live (incluye canary)
uv run pytest -m "not live" -q
```

Los tests canary están en:
`packages/adapters/observability/tests/test_redact.py`

Cubren: `SECRET_CANARY_VALUE`, `PII_CANARY_NAME`, `PII_CANARY_ACCT`, `PII_CANARY_BAL`, y la lectura de canary desde variables de entorno.

## Markers de pytest

| Marker | Descripción |
|--------|-------------|
| `live` | Requiere credenciales bancarias reales (`OPEN_BANCA_LIVE_SMOKE=1`) |
| `unit` | Tests rápidos sin dependencias externas |
| `integration` | Requiere servicios externos (Temporal, DB) |
| `canary` | Gate de seguridad CI — secretos/PII nunca en logs/HAR/OTel (REQ-016) |

## Variables de entorno en CI

El pipeline solo usa `GITHUB_TOKEN` (provisioned automáticamente por GitHub Actions). No se necesitan secretos adicionales para la suite principal.

Para el canary gate no se pasan canary values reales — los tests crean sus propios valores ficticios en fixtures pytest.
