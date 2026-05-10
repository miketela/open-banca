"""Sandbox container garbage collector (ADR-0009, TTL = 6 min).

Identifies and destroys sandbox containers that have been running for more
than ``max_age_seconds`` without a corresponding active job.  Intended to be
called periodically by the Temporal worker (e.g. via a scheduled activity or
a background thread).

All Docker API calls are made through the docker-socket-proxy — never via the
raw socket.

Usage (from within a Temporal activity or background task):
    from open_banca_sandbox.cleanup import SandboxGarbageCollector
    from open_banca_sandbox.runner import DockerSandboxRunner

    gc = SandboxGarbageCollector(runner=DockerSandboxRunner())
    killed = gc.collect(active_job_ids={"job-abc", "job-xyz"})
"""
from __future__ import annotations

import logging
import time

import httpx

from open_banca_sandbox.runner import (
    _CONTAINER_TTL_SECONDS,
    _DOCKER_API_VERSION,
    DockerSandboxRunner,
)

logger = logging.getLogger(__name__)

_MANAGED_LABEL = "com.open-banca.managed=true"
_JOB_ID_LABEL = "com.open-banca.job-id"
_SPAWN_EPOCH_LABEL = "com.open-banca.spawn-epoch"
_COMPONENT_LABEL = "com.open-banca.component"


class SandboxGarbageCollector:
    """Collects orphaned sandbox containers.

    Args:
        runner: An active ``DockerSandboxRunner`` instance (provides the
            httpx client configured for the proxy).
        max_age_seconds: Containers older than this value are considered
            expired even if there is a matching active job.  Defaults to the
            global TTL constant (360 s / 6 min).
    """

    def __init__(
        self,
        runner: DockerSandboxRunner,
        max_age_seconds: int = _CONTAINER_TTL_SECONDS,
    ) -> None:
        self._runner = runner
        self._max_age = max_age_seconds

    def collect(self, active_job_ids: set[str] | None = None) -> list[str]:
        """Kill orphaned and expired sandbox containers.

        A container is a candidate if ALL of:
        - It carries the ``com.open-banca.managed=true`` label.
        - Its ``com.open-banca.component`` label is ``sandbox``.
        - Its job-id is NOT in ``active_job_ids`` (if provided), OR its
          age exceeds ``max_age_seconds``.

        Args:
            active_job_ids: Set of job IDs currently being processed.
                If *None*, only TTL-based expiry is applied.

        Returns:
            List of container IDs that were killed.
        """
        active = active_job_ids or set()
        now = int(time.time())
        killed: list[str] = []

        containers = self._list_sandbox_containers()
        for container in containers:
            cid: str = container.get("Id", "")
            labels: dict[str, str] = container.get("Labels") or {}
            job_id = labels.get(_JOB_ID_LABEL, "")
            spawn_epoch_str = labels.get(_SPAWN_EPOCH_LABEL, "0")

            try:
                spawn_epoch = int(spawn_epoch_str)
            except ValueError:
                spawn_epoch = 0

            age = now - spawn_epoch
            is_orphan = job_id not in active
            is_expired = age > self._max_age

            if is_orphan or is_expired:
                reason = "orphan" if is_orphan else f"expired({age}s)"
                logger.info(
                    "sandbox.gc killing container_id=%s job_id=%s reason=%s",
                    cid,
                    job_id,
                    reason,
                )
                self._force_kill(cid)
                killed.append(cid)

        if killed:
            logger.info("sandbox.gc collected %d containers", len(killed))
        return killed

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _list_sandbox_containers(self) -> list[dict]:  # type: ignore[type-arg]
        """Return all containers carrying the open-banca sandbox label."""
        try:
            resp = self._runner._client.get(
                f"/{_DOCKER_API_VERSION}/containers/json",
                params={
                    "all": "true",
                    "filters": f'{{"label":["{_MANAGED_LABEL}"]}}',
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    "sandbox.gc: failed to list containers: HTTP %s", resp.status_code
                )
                return []
            containers: list[dict] = resp.json()  # type: ignore[type-arg]
            # Filter to sandbox component only
            return [
                c
                for c in containers
                if (c.get("Labels") or {}).get(_COMPONENT_LABEL) == "sandbox"
            ]
        except httpx.HTTPError as exc:
            logger.warning("sandbox.gc: HTTP error listing containers: %s", exc)
            return []

    def _force_kill(self, container_id: str) -> None:
        try:
            self._runner._client.delete(
                f"/{_DOCKER_API_VERSION}/containers/{container_id}",
                params={"force": "true"},
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "sandbox.gc: failed to kill container %s: %s", container_id, exc
            )
