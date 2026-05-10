"""SandboxPort — Docker container isolation per job."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SandboxPort(Protocol):
    """Spawns and manages isolated Docker containers for scrape jobs."""

    def spawn(self, job_id: str) -> str: ...

    def kill(self, container_id: str) -> None: ...

    def attach_network_policy(self, container_id: str, policy: str) -> None: ...
