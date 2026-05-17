"""SandboxPort — Docker container isolation per job."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SandboxToken:
    """Handle returned by SandboxPort.spawn.

    Opaque token that identifies a live sandbox container.  Callers pass
    the entire token to kill() and attach_network_policy() — they must not
    reach inside or construct tokens themselves.
    """

    container_id: str
    container_ip: str
    network_name: str
    sidecar_socket_path: str = ""


@runtime_checkable
class SandboxPort(Protocol):
    """Spawns and manages isolated Docker containers for scrape jobs."""

    def spawn(self, job_id: str, bank_id: str) -> SandboxToken: ...

    def kill(self, token: SandboxToken) -> None: ...

    def attach_network_policy(self, token: SandboxToken, allowed_domains: list[str]) -> None: ...
