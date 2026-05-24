# sandbox/Dockerfile — imagen para containers efímeros per-job (ADR-0009)
#
# Esta imagen se usa SOLO como base para containers spawneados por temporal-worker.
# NO se arranca como servicio en docker-compose.yml (profile: build-only).
#
# El worker spawnea instancias con flags de aislamiento:
#   --cap-drop=ALL --read-only --no-new-privileges --ipc=none
#   --memory=2g --cpus=1.5 --pids-limit=256
#   --network=sandbox-<job_id> (ephemeral, egress allowlist por banco)
#   --tmpfs /tmp:noexec,nosuid,size=512m
#   --tmpfs /secrets:noexec,nosuid,size=64m
#   TTL hard: 6 min
#
# Seguridad (ADR-0009):
#   - Playwright Chromium pinado por SHA digest en CI
#   - Usuario non-root: sandbox (uid=65534 / nobody)
#   - Seccomp default + denylist extra (unshare, keyctl, bpf)
#   - AppArmor profile dedicado (ver docs/04-security/sandbox.md)
#   - Artifacts de job escritos en /artifacts/<job_id>/ solamente

FROM mcr.microsoft.com/playwright/python:v1.51.0-noble AS sandbox-base

# Instalar uv para dependencias Python del sandbox
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /sandbox

# Copiar solo dependencias del paquete sandbox + shared
COPY packages/adapters/sandbox/pyproject.toml packages/adapters/sandbox/
COPY packages/adapters/sandbox/src packages/adapters/sandbox/src/
COPY packages/shared/schemas/pyproject.toml packages/shared/schemas/
COPY packages/shared/schemas/src packages/shared/schemas/src/

# Instalar dependencias en venv aislado
RUN uv venv /sandbox/.venv \
    && uv pip install --python /sandbox/.venv/bin/python \
        playwright \
        pydantic>=2 \
        httpx \
        opentelemetry-sdk \
    && /sandbox/.venv/bin/playwright install chromium --with-deps

# Crear usuario nobody para runtime
RUN useradd --uid 65534 --gid 65534 --no-create-home --shell /sbin/nologin sandbox-user 2>/dev/null || true \
    && mkdir -p /artifacts \
    && chown 65534:65534 /artifacts

ENV PATH="/sandbox/.venv/bin:$PATH"
ENV PYTHONPATH="/sandbox/packages/adapters/sandbox/src:/sandbox/packages/shared/schemas/src"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Job env (inyectadas por worker al spawn)
# JOB_ID, BANK_ID, MAP_PATH, LLM_API_KEYS_FORWARDED

USER 65534

# Entrypoint del job — reemplazado por worker al spawn con job-specific args
ENTRYPOINT ["python", "-m", "adapters.sandbox.runner"]
CMD []

LABEL org.opencontainers.image.title="open-banca-sandbox"
LABEL org.opencontainers.image.description="Ephemeral per-job sandbox with Playwright (ADR-0009)"
LABEL org.opencontainers.image.licenses="AGPL-3.0"
