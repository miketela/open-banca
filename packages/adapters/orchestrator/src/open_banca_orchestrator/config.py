"""Orchestrator configuration via pydantic-settings.

All settings are read from environment variables with the prefix ``OPEN_BANCA_``.

Canonical env-var mapping (matches deployment.md):
  OPEN_BANCA_TEMPORAL_ADDRESS  → temporal_address  (was TEMPORAL_ADDRESS in deployment docs)
  OPEN_BANCA_TEMPORAL_NAMESPACE → temporal_namespace
  OPEN_BANCA_TEMPORAL_TASK_QUEUE → temporal_task_queue

Legacy aliases (no prefix) are intentionally NOT supported here; use OPEN_BANCA_* in all
new deployments.  If you need to map bare TEMPORAL_ADDRESS, set the env var through a
wrapper script or docker-compose env block:
  OPEN_BANCA_TEMPORAL_ADDRESS=${TEMPORAL_ADDRESS:-localhost:7233}
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class OrchestratorSettings(BaseSettings):
    """Runtime configuration for the Temporal worker.

    All fields are resolved from environment variables prefixed with ``OPEN_BANCA_``.
    """

    model_config = SettingsConfigDict(
        env_prefix="OPEN_BANCA_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Temporal connection
    temporal_address: str = "localhost:7233"
    """gRPC address of the Temporal frontend service.

    Deployment doc canonical var: TEMPORAL_ADDRESS.
    Set via OPEN_BANCA_TEMPORAL_ADDRESS in this project.
    """

    temporal_namespace: str = "default"
    """Temporal namespace.  Isolates workflows across environments."""

    temporal_task_queue: str = "open-banca-task-queue"
    """Task queue name consumed by this worker.

    Convention: bump to ``open-banca-task-queue-v2`` for incompatible workflow
    changes (see ADR-0003 mitigation policy).
    """

    # Worker concurrency
    worker_max_concurrent_activities: int = 10
    """Limit on simultaneous activities in this worker process."""

    worker_max_concurrent_workflows: int = 100
    """Limit on simultaneous workflow coroutines."""


def get_settings() -> OrchestratorSettings:
    """Return a cached settings instance (reads env on first call)."""
    return OrchestratorSettings()
