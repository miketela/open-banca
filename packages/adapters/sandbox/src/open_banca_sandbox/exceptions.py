"""Exceptions for the sandbox adapter (ADR-0009)."""

from __future__ import annotations


class SandboxError(Exception):
    """Base error for all sandbox operations."""


class SandboxSpawnError(SandboxError):
    """Raised when a container cannot be created or started."""


class SandboxKillError(SandboxError):
    """Raised when a container cannot be stopped or removed."""


class NetworkPolicyViolation(SandboxError):
    """Raised when the requested network configuration violates the allowlist.

    E.g. caller requests a domain not in the bank's approved list, or the
    docker-socket-proxy rejects a network operation.
    """


class SandboxProxyError(SandboxError):
    """Raised on unexpected HTTP errors from docker-socket-proxy."""

    def __init__(self, method: str, path: str, status_code: int, body: str = "") -> None:
        self.method = method
        self.path = path
        self.status_code = status_code
        self.body = body
        super().__init__(
            f"docker-socket-proxy returned {status_code} for {method} {path}: {body!r}"
        )


class SandboxTimeoutError(SandboxError):
    """Raised when a container operation exceeds its timeout."""
