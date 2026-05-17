"""CleanupSandboxActivity — destroy a sandbox container after job completion.

Idempotent: does NOT fail if the container has already been removed.

Retry policy: max 2 attempts.
Start-to-close timeout: 30s.
"""

from __future__ import annotations

import logging
import os

from pydantic import BaseModel, Field
from temporalio import activity

logger = logging.getLogger(__name__)


class CleanupSandboxInput(BaseModel):
    """Input for CleanupSandboxActivity."""

    container_id: str = Field(description="Docker container ID to destroy")


class CleanupSandboxResult(BaseModel):
    """Result from CleanupSandboxActivity."""

    cleaned: bool = Field(
        default=True,
        description="True if the container was successfully removed (or was already gone)",
    )


class CleanupSandboxActivity:
    """Class-based wrapper (no-op; activity is a module-level function)."""


@activity.defn(name="CleanupSandboxActivity")
async def cleanup_sandbox(input: CleanupSandboxInput) -> CleanupSandboxResult:  # noqa: A002
    """Destroy the sandbox container. Idempotent — ignores 'not found' errors.

    Delegates to ``DockerSandboxRunner.kill()`` via the docker-socket-proxy.
    """
    activity.logger.info("cleanup sandbox: container_id=%s", input.container_id)

    if (
        os.environ.get("OPEN_BANCA_SKIP_SANDBOX", "").strip() in {"1", "true", "yes"}
        or input.container_id.startswith("local-skip-")
    ):
        activity.logger.info("sandbox cleanup skipped (local dev)")
        return CleanupSandboxResult(cleaned=True)

    from open_banca_domain.ports.sandbox_port import SandboxToken  # noqa: PLC0415
    from open_banca_sandbox.runner import DockerSandboxRunner  # noqa: PLC0415

    runner = DockerSandboxRunner()
    token = SandboxToken(
        container_id=input.container_id,
        container_ip="",
        network_name=f"banca-{input.container_id[:12]}",
    )

    try:
        runner.kill(token)
    except Exception as exc:
        err_str = str(exc).lower()
        if "not found" in err_str or "no such" in err_str or "404" in err_str:
            activity.logger.info(
                "sandbox already removed: container_id=%s", input.container_id
            )
            return CleanupSandboxResult(cleaned=True)
        raise

    activity.logger.info("sandbox cleaned: container_id=%s", input.container_id)
    return CleanupSandboxResult(cleaned=True)
