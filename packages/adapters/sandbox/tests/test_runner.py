"""Unit tests for DockerSandboxRunner (ADR-0009, REQ-009, REQ-018).

All tests mock the docker-socket-proxy via respx — no real Docker daemon needed.
Integration tests (requiring OPEN_BANCA_DOCKER_INTEGRATION=1) are at the bottom.

Test matrix:
- test_spawn_config          : verifies full hardening payload sent to proxy
- test_network_allowlist     : spawn calls domains_for_bank, labels include domains
- test_kill_lifecycle        : spawn → kill → container gone (stop + delete)
- test_resource_limits       : CpuQuota, Memory, PidsLimit in HostConfig
- test_caps_drop             : HostConfig.CapDrop == ["ALL"]
- test_no_privileged         : Privileged == False, no host volume binds
- test_socket_proxy_only     : runner never uses raw socket; URL points to proxy
- test_readonly_rootfs        : ReadonlyRootfs == True
- test_no_secrets_in_env     : Env list contains no secret-like values
- test_nonroot_user           : User == "65534:65534"
- test_tmpfs_mounts           : /tmp, /secrets, /downloads in Tmpfs
- test_ipc_none               : IpcMode == "none"
- test_network_is_internal    : network create payload has Internal == True
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator

import httpx
import pytest
import respx

from open_banca_sandbox.exceptions import SandboxKillError, SandboxSpawnError
from open_banca_sandbox.runner import (
    _CPU_QUOTA,
    _DOCKER_API_VERSION,
    _MEMORY_BYTES,
    _PIDS_LIMIT,
    DockerSandboxRunner,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROXY_URL = "http://docker-socket-proxy:2375"
_JOB_ID = "job-test-001"
_BANK_ID = "banco_general"
_CONTAINER_ID = "abc123def456"
_NETWORK_ID = "net789"


def _networks_create_url() -> str:
    return f"{_PROXY_URL}/{_DOCKER_API_VERSION}/networks/create"


def _containers_create_url() -> str:
    return f"{_PROXY_URL}/{_DOCKER_API_VERSION}/containers/create"


def _containers_start_url(cid: str) -> str:
    return f"{_PROXY_URL}/{_DOCKER_API_VERSION}/containers/{cid}/start"


def _containers_inspect_url(cid: str) -> str:
    return f"{_PROXY_URL}/{_DOCKER_API_VERSION}/containers/{cid}/json"


def _containers_stop_url(cid: str) -> str:
    return f"{_PROXY_URL}/{_DOCKER_API_VERSION}/containers/{cid}/stop"


def _containers_delete_url(cid: str) -> str:
    return f"{_PROXY_URL}/{_DOCKER_API_VERSION}/containers/{cid}"


def _networks_delete_url(name: str) -> str:
    return f"{_PROXY_URL}/{_DOCKER_API_VERSION}/networks/{name}"


def _inspect_response(cid: str, network_name: str, ip: str = "172.20.0.2") -> dict:  # type: ignore[type-arg]
    return {
        "Id": cid,
        "NetworkSettings": {
            "Networks": {
                network_name: {"IPAddress": ip},
            }
        },
    }


# ---------------------------------------------------------------------------
# Fixture: runner + respx mock router
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_router() -> Generator[respx.MockRouter, None, None]:
    with respx.mock(base_url=_PROXY_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture()
def runner() -> Generator[DockerSandboxRunner, None, None]:
    r = DockerSandboxRunner(proxy_url=_PROXY_URL, image="open-banca-sandbox:test")
    yield r
    r.close()


def _setup_happy_path(
    router: respx.MockRouter,
    job_id: str = _JOB_ID,
    bank_id: str = _BANK_ID,
    cid: str = _CONTAINER_ID,
) -> None:
    """Register all routes for a successful spawn cycle."""
    network_name = f"sandbox-{job_id}"

    router.post(_networks_create_url()).mock(
        return_value=httpx.Response(201, json={"Id": _NETWORK_ID, "Warning": ""})
    )
    router.post(_containers_create_url()).mock(
        return_value=httpx.Response(201, json={"Id": cid, "Warnings": []})
    )
    router.post(_containers_start_url(cid)).mock(return_value=httpx.Response(204))
    router.get(_containers_inspect_url(cid)).mock(
        return_value=httpx.Response(200, json=_inspect_response(cid, network_name))
    )


# ===========================================================================
# Spawn config tests
# ===========================================================================


def test_spawn_config_sends_to_proxy(
    mock_router: respx.MockRouter, runner: DockerSandboxRunner
) -> None:
    """spawn() must send requests exclusively to the docker-socket-proxy URL."""
    network_name = f"sandbox-{_JOB_ID}"
    _setup_happy_path(mock_router)

    mock_router.post(_containers_stop_url(_CONTAINER_ID)).mock(return_value=httpx.Response(204))
    mock_router.delete(_containers_delete_url(_CONTAINER_ID)).mock(return_value=httpx.Response(204))
    mock_router.delete(_networks_delete_url(network_name)).mock(return_value=httpx.Response(204))

    token = runner.spawn(_JOB_ID, _BANK_ID)
    assert token.container_id == _CONTAINER_ID


def test_spawn_config_hardening_flags(
    mock_router: respx.MockRouter, runner: DockerSandboxRunner
) -> None:
    """spawn() must set all security-hardening fields in the create payload."""
    network_name = f"sandbox-{_JOB_ID}"
    captured: dict = {}  # type: ignore[type-arg]

    def capture_create(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json={"Id": _CONTAINER_ID, "Warnings": []})

    router = mock_router
    router.post(_networks_create_url()).mock(
        return_value=httpx.Response(201, json={"Id": _NETWORK_ID})
    )
    router.post(_containers_create_url()).mock(side_effect=capture_create)
    router.post(_containers_start_url(_CONTAINER_ID)).mock(return_value=httpx.Response(204))
    router.get(_containers_inspect_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(200, json=_inspect_response(_CONTAINER_ID, network_name))
    )

    runner.spawn(_JOB_ID, _BANK_ID)

    hc = captured["HostConfig"]
    assert hc["ReadonlyRootfs"] is True, "rootfs must be read-only"
    assert hc["CapDrop"] == ["ALL"], "must drop ALL capabilities"
    assert hc["CapAdd"] == [], "must not add any capabilities"
    assert hc["Privileged"] is False, "must not be privileged"
    assert "no-new-privileges:true" in hc["SecurityOpt"]
    assert hc["Memory"] == _MEMORY_BYTES
    assert hc["CpuQuota"] == _CPU_QUOTA
    assert hc["PidsLimit"] == _PIDS_LIMIT
    assert hc["IpcMode"] == "none"
    assert hc["Binds"] == [], "no host volume binds"
    assert hc["Mounts"] == [], "no host mounts"


# ===========================================================================
# Resource limits
# ===========================================================================


def test_resource_limits(mock_router: respx.MockRouter, runner: DockerSandboxRunner) -> None:
    """HostConfig must declare CPU, Memory, and PidsLimit within spec."""
    network_name = f"sandbox-{_JOB_ID}"
    captured: dict = {}  # type: ignore[type-arg]

    mock_router.post(_networks_create_url()).mock(
        return_value=httpx.Response(201, json={"Id": _NETWORK_ID})
    )
    mock_router.post(_containers_create_url()).mock(
        side_effect=lambda req: (
            (captured.update(json.loads(req.content)) or None)
            or httpx.Response(201, json={"Id": _CONTAINER_ID})
        )
    )
    mock_router.post(_containers_start_url(_CONTAINER_ID)).mock(return_value=httpx.Response(204))
    mock_router.get(_containers_inspect_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(200, json=_inspect_response(_CONTAINER_ID, network_name))
    )

    runner.spawn(_JOB_ID, _BANK_ID)
    hc = captured["HostConfig"]
    # 1.5 CPUs = 150000 quota / 100000 period
    assert hc["CpuQuota"] == 150_000
    assert hc["CpuPeriod"] == 100_000
    assert hc["Memory"] == 2 * 1024 * 1024 * 1024  # 2 GiB
    assert hc["PidsLimit"] == 256


# ===========================================================================
# Caps drop
# ===========================================================================


def test_caps_drop(mock_router: respx.MockRouter, runner: DockerSandboxRunner) -> None:
    """HostConfig.CapDrop must equal ['ALL']."""
    network_name = f"sandbox-{_JOB_ID}"
    captured: dict = {}  # type: ignore[type-arg]

    mock_router.post(_networks_create_url()).mock(
        return_value=httpx.Response(201, json={"Id": _NETWORK_ID})
    )
    mock_router.post(_containers_create_url()).mock(
        side_effect=lambda req: (
            (captured.update(json.loads(req.content)) or None)
            or httpx.Response(201, json={"Id": _CONTAINER_ID})
        )
    )
    mock_router.post(_containers_start_url(_CONTAINER_ID)).mock(return_value=httpx.Response(204))
    mock_router.get(_containers_inspect_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(200, json=_inspect_response(_CONTAINER_ID, network_name))
    )

    runner.spawn(_JOB_ID, _BANK_ID)
    assert captured["HostConfig"]["CapDrop"] == ["ALL"]


# ===========================================================================
# No privileged / no host volumes
# ===========================================================================


def test_no_privileged(mock_router: respx.MockRouter, runner: DockerSandboxRunner) -> None:
    """Privileged must be False and no host volume binds."""
    network_name = f"sandbox-{_JOB_ID}"
    captured: dict = {}  # type: ignore[type-arg]

    mock_router.post(_networks_create_url()).mock(
        return_value=httpx.Response(201, json={"Id": _NETWORK_ID})
    )
    mock_router.post(_containers_create_url()).mock(
        side_effect=lambda req: (
            (captured.update(json.loads(req.content)) or None)
            or httpx.Response(201, json={"Id": _CONTAINER_ID})
        )
    )
    mock_router.post(_containers_start_url(_CONTAINER_ID)).mock(return_value=httpx.Response(204))
    mock_router.get(_containers_inspect_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(200, json=_inspect_response(_CONTAINER_ID, network_name))
    )

    runner.spawn(_JOB_ID, _BANK_ID)
    hc = captured["HostConfig"]
    assert hc["Privileged"] is False
    assert hc["Binds"] == []
    assert hc["Mounts"] == []


# ===========================================================================
# Read-only rootfs + tmpfs
# ===========================================================================


def test_readonly_rootfs(mock_router: respx.MockRouter, runner: DockerSandboxRunner) -> None:
    """ReadonlyRootfs must be True."""
    network_name = f"sandbox-{_JOB_ID}"
    captured: dict = {}  # type: ignore[type-arg]

    mock_router.post(_networks_create_url()).mock(
        return_value=httpx.Response(201, json={"Id": _NETWORK_ID})
    )
    mock_router.post(_containers_create_url()).mock(
        side_effect=lambda req: (
            (captured.update(json.loads(req.content)) or None)
            or httpx.Response(201, json={"Id": _CONTAINER_ID})
        )
    )
    mock_router.post(_containers_start_url(_CONTAINER_ID)).mock(return_value=httpx.Response(204))
    mock_router.get(_containers_inspect_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(200, json=_inspect_response(_CONTAINER_ID, network_name))
    )

    runner.spawn(_JOB_ID, _BANK_ID)
    assert captured["HostConfig"]["ReadonlyRootfs"] is True


def test_tmpfs_mounts(mock_router: respx.MockRouter, runner: DockerSandboxRunner) -> None:
    """Tmpfs must cover /tmp, /secrets, /downloads."""
    network_name = f"sandbox-{_JOB_ID}"
    captured: dict = {}  # type: ignore[type-arg]

    mock_router.post(_networks_create_url()).mock(
        return_value=httpx.Response(201, json={"Id": _NETWORK_ID})
    )
    mock_router.post(_containers_create_url()).mock(
        side_effect=lambda req: (
            (captured.update(json.loads(req.content)) or None)
            or httpx.Response(201, json={"Id": _CONTAINER_ID})
        )
    )
    mock_router.post(_containers_start_url(_CONTAINER_ID)).mock(return_value=httpx.Response(204))
    mock_router.get(_containers_inspect_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(200, json=_inspect_response(_CONTAINER_ID, network_name))
    )

    runner.spawn(_JOB_ID, _BANK_ID)
    tmpfs = captured["HostConfig"]["Tmpfs"]
    assert "/tmp" in tmpfs
    assert "/secrets" in tmpfs
    assert "/downloads" in tmpfs
    assert "noexec" in tmpfs["/tmp"]
    assert "nosuid" in tmpfs["/tmp"]


# ===========================================================================
# Non-root user
# ===========================================================================


def test_nonroot_user(mock_router: respx.MockRouter, runner: DockerSandboxRunner) -> None:
    """Container must run as uid:gid 65534:65534 (nobody)."""
    network_name = f"sandbox-{_JOB_ID}"
    captured: dict = {}  # type: ignore[type-arg]

    mock_router.post(_networks_create_url()).mock(
        return_value=httpx.Response(201, json={"Id": _NETWORK_ID})
    )
    mock_router.post(_containers_create_url()).mock(
        side_effect=lambda req: (
            (captured.update(json.loads(req.content)) or None)
            or httpx.Response(201, json={"Id": _CONTAINER_ID})
        )
    )
    mock_router.post(_containers_start_url(_CONTAINER_ID)).mock(return_value=httpx.Response(204))
    mock_router.get(_containers_inspect_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(200, json=_inspect_response(_CONTAINER_ID, network_name))
    )

    runner.spawn(_JOB_ID, _BANK_ID)
    assert captured["User"] == "65534:65534"


# ===========================================================================
# No secrets in env
# ===========================================================================


def test_no_secrets_in_env(mock_router: respx.MockRouter, runner: DockerSandboxRunner) -> None:
    """Env vars must not contain secrets — only JOB_ID and BANK_ID."""
    network_name = f"sandbox-{_JOB_ID}"
    captured: dict = {}  # type: ignore[type-arg]

    mock_router.post(_networks_create_url()).mock(
        return_value=httpx.Response(201, json={"Id": _NETWORK_ID})
    )
    mock_router.post(_containers_create_url()).mock(
        side_effect=lambda req: (
            (captured.update(json.loads(req.content)) or None)
            or httpx.Response(201, json={"Id": _CONTAINER_ID})
        )
    )
    mock_router.post(_containers_start_url(_CONTAINER_ID)).mock(return_value=httpx.Response(204))
    mock_router.get(_containers_inspect_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(200, json=_inspect_response(_CONTAINER_ID, network_name))
    )

    runner.spawn(_JOB_ID, _BANK_ID)
    env_list: list[str] = captured.get("Env", [])
    # Only JOB_ID and BANK_ID are allowed
    allowed_prefixes = {"JOB_ID=", "BANK_ID="}
    for env_var in env_list:
        prefix = env_var.split("=")[0] + "="
        assert prefix in allowed_prefixes, f"Unexpected env var leaked: {env_var}"
    # Must not contain tokens, passwords, keys
    secret_keywords = ["KEY", "SECRET", "PASSWORD", "TOKEN", "PASSPHRASE"]
    for env_var in env_list:
        for kw in secret_keywords:
            assert kw not in env_var.upper() or env_var.startswith("BANK_ID"), (
                f"Secret-looking env var in container: {env_var}"
            )


# ===========================================================================
# Network allowlist
# ===========================================================================


def test_network_allowlist_labels(
    mock_router: respx.MockRouter, runner: DockerSandboxRunner
) -> None:
    """Container labels must include banco_general's allowed domains."""
    network_name = f"sandbox-{_JOB_ID}"
    captured: dict = {}  # type: ignore[type-arg]

    mock_router.post(_networks_create_url()).mock(
        return_value=httpx.Response(201, json={"Id": _NETWORK_ID})
    )
    mock_router.post(_containers_create_url()).mock(
        side_effect=lambda req: (
            (captured.update(json.loads(req.content)) or None)
            or httpx.Response(201, json={"Id": _CONTAINER_ID})
        )
    )
    mock_router.post(_containers_start_url(_CONTAINER_ID)).mock(return_value=httpx.Response(204))
    mock_router.get(_containers_inspect_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(200, json=_inspect_response(_CONTAINER_ID, network_name))
    )

    runner.spawn(_JOB_ID, _BANK_ID)
    labels = captured.get("Labels", {})
    allowed_domains_label = labels.get("com.open-banca.allowed-domains", "")
    assert "bancogeneral.com" in allowed_domains_label
    assert "www.bancogeneral.com" in allowed_domains_label
    assert "api.anthropic.com" in allowed_domains_label
    assert "api.deepseek.com" in allowed_domains_label


def test_network_is_internal(mock_router: respx.MockRouter, runner: DockerSandboxRunner) -> None:
    """Docker network must be created as Internal=True (no default gateway)."""
    network_name = f"sandbox-{_JOB_ID}"
    captured_network: dict = {}  # type: ignore[type-arg]

    def capture_network(req: httpx.Request) -> httpx.Response:
        captured_network.update(json.loads(req.content))
        return httpx.Response(201, json={"Id": _NETWORK_ID})

    mock_router.post(_networks_create_url()).mock(side_effect=capture_network)
    mock_router.post(_containers_create_url()).mock(
        return_value=httpx.Response(201, json={"Id": _CONTAINER_ID})
    )
    mock_router.post(_containers_start_url(_CONTAINER_ID)).mock(return_value=httpx.Response(204))
    mock_router.get(_containers_inspect_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(200, json=_inspect_response(_CONTAINER_ID, network_name))
    )

    runner.spawn(_JOB_ID, _BANK_ID)
    assert captured_network.get("Internal") is True


# ===========================================================================
# IPC isolation
# ===========================================================================


def test_ipc_none(mock_router: respx.MockRouter, runner: DockerSandboxRunner) -> None:
    """IpcMode must be 'none'."""
    network_name = f"sandbox-{_JOB_ID}"
    captured: dict = {}  # type: ignore[type-arg]

    mock_router.post(_networks_create_url()).mock(
        return_value=httpx.Response(201, json={"Id": _NETWORK_ID})
    )
    mock_router.post(_containers_create_url()).mock(
        side_effect=lambda req: (
            (captured.update(json.loads(req.content)) or None)
            or httpx.Response(201, json={"Id": _CONTAINER_ID})
        )
    )
    mock_router.post(_containers_start_url(_CONTAINER_ID)).mock(return_value=httpx.Response(204))
    mock_router.get(_containers_inspect_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(200, json=_inspect_response(_CONTAINER_ID, network_name))
    )

    runner.spawn(_JOB_ID, _BANK_ID)
    assert captured["HostConfig"]["IpcMode"] == "none"


def test_attach_network_policy_rejects_unknown_domain(
    mock_router: respx.MockRouter, runner: DockerSandboxRunner
) -> None:
    """attach_network_policy raises when domain is outside bank allowlist."""
    from open_banca_domain.ports.sandbox_port import SandboxToken
    from open_banca_sandbox.exceptions import NetworkPolicyViolation

    network_name = f"sandbox-{_JOB_ID}"
    inspect_payload = {
        "Id": _CONTAINER_ID,
        "Config": {"Labels": {"com.open-banca.bank-id": _BANK_ID}},
        "NetworkSettings": {"Networks": {network_name: {"IPAddress": "172.20.0.2"}}},
    }
    mock_router.get(_containers_inspect_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(200, json=inspect_payload)
    )
    token = SandboxToken(
        container_id=_CONTAINER_ID,
        container_ip="172.20.0.2",
        network_name=network_name,
    )
    with pytest.raises(NetworkPolicyViolation, match="not in bank allowlist"):
        runner.attach_network_policy(token, ["evil.example.com"])


def test_attach_network_policy_accepts_bank_and_llm_domains(
    mock_router: respx.MockRouter, runner: DockerSandboxRunner
) -> None:
    """attach_network_policy accepts full bank+LLM allowlist."""
    from open_banca_domain.ports.sandbox_port import SandboxToken
    from open_banca_sandbox.network import domains_for_bank

    network_name = f"sandbox-{_JOB_ID}"
    inspect_payload = {
        "Id": _CONTAINER_ID,
        "Config": {"Labels": {"com.open-banca.bank-id": _BANK_ID}},
        "NetworkSettings": {"Networks": {network_name: {"IPAddress": "172.20.0.2"}}},
    }
    mock_router.get(_containers_inspect_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(200, json=inspect_payload)
    )
    token = SandboxToken(
        container_id=_CONTAINER_ID,
        container_ip="172.20.0.2",
        network_name=network_name,
    )
    runner.attach_network_policy(token, domains_for_bank(_BANK_ID, include_llm=True))


# ===========================================================================


def test_kill_lifecycle(mock_router: respx.MockRouter, runner: DockerSandboxRunner) -> None:
    """spawn() then kill() must stop and delete the container."""
    network_name = f"sandbox-{_JOB_ID}"
    _setup_happy_path(mock_router)

    stop_route = mock_router.post(_containers_stop_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(204)
    )
    delete_route = mock_router.delete(_containers_delete_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(204)
    )
    mock_router.delete(_networks_delete_url(network_name)).mock(return_value=httpx.Response(204))

    token = runner.spawn(_JOB_ID, _BANK_ID)
    runner.kill(token)

    assert stop_route.called
    assert delete_route.called


def test_kill_with_already_stopped_container(
    mock_router: respx.MockRouter, runner: DockerSandboxRunner
) -> None:
    """kill() must succeed even if container is already stopped (304)."""
    network_name = f"sandbox-{_JOB_ID}"
    _setup_happy_path(mock_router)

    mock_router.post(_containers_stop_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(304)  # already stopped
    )
    mock_router.delete(_containers_delete_url(_CONTAINER_ID)).mock(return_value=httpx.Response(204))
    mock_router.delete(_networks_delete_url(network_name)).mock(return_value=httpx.Response(204))

    token = runner.spawn(_JOB_ID, _BANK_ID)
    runner.kill(token)  # must not raise


def test_kill_raises_on_delete_failure(
    mock_router: respx.MockRouter, runner: DockerSandboxRunner
) -> None:
    """kill() raises SandboxKillError when container delete returns 500."""
    network_name = f"sandbox-{_JOB_ID}"
    _setup_happy_path(mock_router)

    mock_router.post(_containers_stop_url(_CONTAINER_ID)).mock(return_value=httpx.Response(204))
    mock_router.delete(_containers_delete_url(_CONTAINER_ID)).mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    mock_router.delete(_networks_delete_url(network_name)).mock(return_value=httpx.Response(204))

    token = runner.spawn(_JOB_ID, _BANK_ID)
    with pytest.raises(SandboxKillError):
        runner.kill(token)


# ===========================================================================
# socket-proxy-only: verify runner URL never points to raw socket
# ===========================================================================


def test_socket_proxy_only() -> None:
    """DockerSandboxRunner must never use /var/run/docker.sock directly.

    The constructor stores _base_url; it must be HTTP (tcp proxy), not a
    Unix socket path.
    """
    runner = DockerSandboxRunner(proxy_url="http://docker-socket-proxy:2375")
    assert runner._base_url.startswith("http://"), (
        "runner must use HTTP proxy URL, not raw docker socket"
    )
    assert "/var/run" not in runner._base_url, (
        "runner must not reference /var/run/docker.sock directly"
    )
    assert "docker.sock" not in runner._base_url
    runner.close()


def test_socket_proxy_only_default_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default URL sourced from DOCKER_HOST env must also be HTTP-based."""
    monkeypatch.setenv("DOCKER_HOST", "tcp://docker-socket-proxy:2375")
    r = DockerSandboxRunner()
    assert r._base_url.startswith("http://")
    assert "docker.sock" not in r._base_url
    r.close()


# ===========================================================================
# Spawn failure handling
# ===========================================================================


def test_spawn_raises_on_network_create_failure(
    mock_router: respx.MockRouter, runner: DockerSandboxRunner
) -> None:
    """SandboxSpawnError raised when network creation returns non-201."""
    mock_router.post(_networks_create_url()).mock(
        return_value=httpx.Response(500, text="daemon error")
    )
    with pytest.raises(SandboxSpawnError):
        runner.spawn(_JOB_ID, _BANK_ID)


def test_spawn_raises_on_container_create_failure(
    mock_router: respx.MockRouter, runner: DockerSandboxRunner
) -> None:
    """SandboxSpawnError raised when container creation returns non-201."""
    mock_router.post(_networks_create_url()).mock(
        return_value=httpx.Response(201, json={"Id": _NETWORK_ID})
    )
    mock_router.post(_containers_create_url()).mock(
        return_value=httpx.Response(500, text="image not found")
    )
    mock_router.delete(f"{_PROXY_URL}/{_DOCKER_API_VERSION}/networks/sandbox-{_JOB_ID}").mock(
        return_value=httpx.Response(204)
    )
    with pytest.raises(SandboxSpawnError):
        runner.spawn(_JOB_ID, _BANK_ID)


# ===========================================================================
# Integration tests (env-gated)
# ===========================================================================

_DOCKER_INTEGRATION = os.getenv("OPEN_BANCA_DOCKER_INTEGRATION", "0") == "1"

skip_without_docker = pytest.mark.skipif(
    not _DOCKER_INTEGRATION,
    reason=(
        "Integration tests require a real docker-socket-proxy. "
        "Set OPEN_BANCA_DOCKER_INTEGRATION=1 to enable."
    ),
)


@skip_without_docker
def test_integration_real_spawn_and_kill() -> None:
    """Integration: real spawn then kill against a live docker-socket-proxy."""
    proxy_host = os.getenv("DOCKER_PROXY_HOST", "localhost")
    proxy_port = os.getenv("DOCKER_PROXY_PORT", "2375")
    proxy_url = f"http://{proxy_host}:{proxy_port}"

    r = DockerSandboxRunner(proxy_url=proxy_url)
    token = r.spawn("integration-job-001", "banco_general")
    assert token.container_id
    assert token.network_name == "sandbox-integration-job-001"

    r.kill(token)
    # Verify container is gone
    resp = r._client.get(f"/{_DOCKER_API_VERSION}/containers/{token.container_id}/json")
    assert resp.status_code == 404, "Container should be removed after kill()"
    r.close()
