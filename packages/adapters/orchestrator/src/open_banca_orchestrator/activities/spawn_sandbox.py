"""SpawnSandboxActivity — create an isolated Docker container for a scrape job.

Wraps ``open_banca_sandbox.DockerSandboxRunner.spawn()`` as a Temporal activity.

Retry policy: max 2 attempts (Docker may need image pull on first run).
Start-to-close timeout: 60s.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field
from temporalio import activity

logger = logging.getLogger(__name__)


class SpawnSandboxInput(BaseModel):
    """Input for SpawnSandboxActivity."""

    job_id: str = Field(description="Unique job identifier")
    bank_id: str = Field(description="Bank identifier for network policy")


class SpawnSandboxResult(BaseModel):
    """Result from SpawnSandboxActivity."""

    container_id: str = Field(description="Docker container ID of the spawned sandbox")
    sidecar_socket_path: str = Field(
        default="/run/banca/sidecar.sock",
        description="Unix socket path inside the container for BrowserSidecar IPC",
    )


class SpawnSandboxActivity:
    """Class-based wrapper (no-op; activity is a module-level function)."""


@activity.defn(name="SpawnSandboxActivity")
async def spawn_sandbox(input: SpawnSandboxInput) -> SpawnSandboxResult:  # noqa: A002
    """Spawn an isolated Docker sandbox container for the scrape job.

    Delegates to ``DockerSandboxRunner.spawn()`` which creates a hardened
    container with read-only rootfs, capability drops, resource limits,
    and an isolated network (ADR-0009).

    Raises:
        SandboxSpawnError: If Docker API fails or image pull fails.
    """
    activity.logger.info(
        "spawning sandbox: job_id=%s bank_id=%s", input.job_id, input.bank_id
    )

    from open_banca_sandbox.runner import DockerSandboxRunner  # noqa: PLC0415

    runner = DockerSandboxRunner()
    token = runner.spawn(job_id=input.job_id, bank_id=input.bank_id)

    activity.logger.info(
        "sandbox spawned: container_id=%s", token.container_id
    )

    return SpawnSandboxResult(
        container_id=token.container_id,
        sidecar_socket_path=token.sidecar_socket_path or "/run/banca/sidecar.sock",
    )
