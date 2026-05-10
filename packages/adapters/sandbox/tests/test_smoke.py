"""Smoke test: verifies the open_banca_sandbox package is importable
and that core symbols are exported."""
from __future__ import annotations


def test_open_banca_sandbox_imports() -> None:
    import open_banca_sandbox  # noqa: F401

    assert open_banca_sandbox.__name__ == "open_banca_sandbox"


def test_core_symbols_exported() -> None:
    from open_banca_sandbox import (
        DockerSandboxRunner,
        NetworkPolicyViolation,
        SandboxError,
        SandboxGarbageCollector,
        SandboxKillError,
        SandboxProxyError,
        SandboxSpawnError,
        SandboxTimeoutError,
        domains_for_bank,
    )

    assert DockerSandboxRunner is not None
    assert SandboxGarbageCollector is not None
    assert SandboxError is not None
    assert SandboxSpawnError is not None
    assert SandboxKillError is not None
    assert SandboxProxyError is not None
    assert SandboxTimeoutError is not None
    assert NetworkPolicyViolation is not None
    assert domains_for_bank is not None


def test_sandbox_token_is_importable() -> None:
    from open_banca_domain.ports.sandbox_port import SandboxToken

    token = SandboxToken(
        container_id="abc",
        container_ip="10.0.0.1",
        network_name="sandbox-job-1",
    )
    assert token.container_id == "abc"
    assert token.network_name == "sandbox-job-1"
    assert token.sidecar_socket_path == ""
