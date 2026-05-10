# Dockerfile — open-banca multi-stage build
#
# Stages:
#   builder  — instala dependencias con uv, genera requirements freeze
#   runtime  — imagen final python:3.12-slim, non-root, no shell, read-only rootfs
#
# El mismo target "runtime" sirve para api y temporal-worker.
# docker-compose.yml diferencia el entrypoint vía `command:` override.
#
# Construcción:
#   docker build --target runtime -t open-banca:latest .
#
# Para sandbox-runner ver sandbox/Dockerfile (spawned per-job, ADR-0009).
#
# Seguridad:
#   - Usuario no-root: open-banca (uid=10001)
#   - No shell en runtime (ash/bash no incluido)
#   - read_only: true + tmpfs en compose (operador config)
#   - no-new-privileges: via securityopt en compose o docker run

# =============================================================================
# Stage 1: builder — instala deps con uv
# =============================================================================
FROM python:3.12-slim AS builder

# Instalar uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY --from=ghcr.io/astral-sh/uv:latest /uvx /usr/local/bin/uvx

WORKDIR /build

# Copiar archivos de configuración de workspace primero (layer cache)
COPY pyproject.toml uv.lock ./
COPY packages/domain/pyproject.toml packages/domain/
COPY packages/application/pyproject.toml packages/application/
COPY packages/adapters/api/pyproject.toml packages/adapters/api/
COPY packages/adapters/orchestrator/pyproject.toml packages/adapters/orchestrator/
COPY packages/adapters/browser/pyproject.toml packages/adapters/browser/
COPY packages/adapters/llm/pyproject.toml packages/adapters/llm/
COPY packages/adapters/storage/pyproject.toml packages/adapters/storage/
COPY packages/adapters/parsing/pyproject.toml packages/adapters/parsing/
COPY packages/adapters/sandbox/pyproject.toml packages/adapters/sandbox/
COPY packages/adapters/observability/pyproject.toml packages/adapters/observability/
COPY packages/banks/banco_general/pyproject.toml packages/banks/banco_general/
COPY packages/shared/schemas/pyproject.toml packages/shared/schemas/

# Instalar dependencias en /build/.venv (sin editable installs para runtime)
ENV UV_PROJECT_ENVIRONMENT=/build/.venv
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

RUN uv sync --frozen --no-dev --all-packages

# Copiar source completo para bytecode compilation
COPY packages/ packages/

# Pre-compilar bytecode (reduce startup time)
RUN uv run python -m compileall -q packages/ || true

# =============================================================================
# Stage 2: runtime — imagen final mínima, non-root
# =============================================================================
FROM python:3.12-slim AS runtime

# Instalar solo lo necesario en runtime: curl para healthcheck, no shell
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Crear usuario non-root sin shell
RUN groupadd --gid 10001 open-banca \
    && useradd --uid 10001 --gid 10001 \
               --no-create-home \
               --shell /sbin/nologin \
               open-banca

# Directorios de runtime (volúmenes se montan aquí)
RUN mkdir -p /data /maps /artifacts \
    && chown open-banca:open-banca /data /maps /artifacts

WORKDIR /app

# Copiar virtualenv construido en builder
COPY --from=builder --chown=open-banca:open-banca /build/.venv /app/.venv

# Copiar source packages
COPY --from=builder --chown=open-banca:open-banca /build/packages /app/packages

# PATH para el venv
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/packages/domain/src:/app/packages/application/src:/app/packages/adapters/api/src:/app/packages/adapters/orchestrator/src:/app/packages/adapters/browser/src:/app/packages/adapters/llm/src:/app/packages/adapters/storage/src:/app/packages/adapters/parsing/src:/app/packages/adapters/sandbox/src:/app/packages/adapters/observability/src:/app/packages/banks/banco_general/src:/app/packages/shared/schemas/src"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Runtime defaults
ENV DATA_DIR=/data
ENV MAPS_DIR=/maps
ENV ARTIFACTS_DIR=/artifacts

USER open-banca

# Default command: api (overridden to worker in docker-compose.yml for temporal-worker)
CMD ["uvicorn", "adapters.api.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

EXPOSE 8080

# Labels OCI
LABEL org.opencontainers.image.title="open-banca"
LABEL org.opencontainers.image.description="Self-hosted API for automated bank data capture — Panama"
LABEL org.opencontainers.image.licenses="AGPL-3.0"
LABEL org.opencontainers.image.source="https://github.com/open-banca/open-banca"
