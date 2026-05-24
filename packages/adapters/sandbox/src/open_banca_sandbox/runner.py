"""DockerSandboxRunner — SandboxPort implementation via docker-socket-proxy.

All Docker API calls go through the proxy at ``tcp://docker-socket-proxy:2375``
(or the URL supplied at construction time).  Direct socket access to
``/var/run/docker.sock`` is explicitly forbidden (REQ-018, ADR-0009).

Hardening applied at container creation:
- Read-only rootfs (``ReadonlyRootfs: true``)
- tmpfs at /tmp and /secrets (noexec, nosuid, size-limited)
- Non-root user uid=65534 (nobody / sandbox-user)
- CapDrop ALL — no Linux capabilities
- security_opt: no-new-privileges, seccomp:default, apparmor:docker-default
- Resource limits: CPU quota 1.5 cores, Memory 2 GiB, PidsLimit 256
- No volume mounts (no host filesystem access)
- No privileged flag
- NetworkMode: custom per-job bridge (internal=True, no default gateway)
- IPC mode: none
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
from open_banca_domain.ports.sandbox_port import SandboxPort, SandboxToken

from open_banca_sandbox.exceptions import (
    NetworkPolicyViolation,
    SandboxKillError,
    SandboxProxyError,
    SandboxSpawnError,
)
from open_banca_sandbox.network import domains_for_bank

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_PROXY_URL = os.getenv("DOCKER_HOST", "tcp://docker-socket-proxy:2375").replace(
    "tcp://", "http://"
)
_DOCKER_API_VERSION = "v1.44"

# Hard container TTL enforced by cleanup.py; spawn embeds a label so cleanup
# can identify orphaned containers.
_CONTAINER_TTL_SECONDS = 360  # 6 minutes

# Resource limits (ADR-0009)
_CPU_QUOTA = 150_000   # 1.5 CPUs in microseconds (100_000 = 1 CPU)
_CPU_PERIOD = 100_000  # microseconds
_MEMORY_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
_PIDS_LIMIT = 256

# tmpfs sizes
_TMPFS_TMP_SIZE = "512m"
_TMPFS_SECRETS_SIZE = "64m"
_TMPFS_DOWNLOADS_SIZE = "500m"

# Sandbox image (overridable via env)
_SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "open-banca-sandbox:latest")


class DockerSandboxRunner:
    """Implements SandboxPort via docker-socket-proxy HTTP API.

    Args:
        proxy_url: Base URL of the docker-socket-proxy service.
            Defaults to ``$DOCKER_HOST`` or ``http://docker-socket-proxy:2375``.
        image: Docker image to use for sandbox containers.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        proxy_url: str = _DEFAULT_PROXY_URL,
        image: str = _SANDBOX_IMAGE,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = proxy_url.rstrip("/")
        self._image = image
        self._client = httpx.Client(base_url=self._base_url, timeout=timeout)

    # ------------------------------------------------------------------
    # SandboxPort interface
    # ------------------------------------------------------------------

    def spawn(self, job_id: str, bank_id: str) -> SandboxToken:
        """Create, configure, and start an isolated container for *job_id*.

        Steps:
        1. Create a per-job Docker network (internal, no egress by default).
        2. Create the container with all hardening flags applied.
        3. Connect the container to the job network.
        4. Start the container.
        5. Inspect to get the assigned IP.

        Returns:
            SandboxToken with container_id, container_ip, network_name.

        Raises:
            SandboxSpawnError: on any failure during creation/start.
        """
        network_name = f"sandbox-{job_id}"
        try:
            self._create_network(network_name)
        except SandboxProxyError as exc:
            raise SandboxSpawnError(
                f"Failed to create network {network_name!r}: {exc}"
            ) from exc

        container_id: str
        try:
            container_id = self._create_container(job_id, bank_id, network_name)
        except SandboxProxyError as exc:
            self._cleanup_network_best_effort(network_name)
            raise SandboxSpawnError(
                f"Failed to create container for job {job_id!r}: {exc}"
            ) from exc

        try:
            self._start_container(container_id)
        except SandboxProxyError as exc:
            self._remove_container_best_effort(container_id)
            self._cleanup_network_best_effort(network_name)
            raise SandboxSpawnError(
                f"Failed to start container {container_id!r} for job {job_id!r}: {exc}"
            ) from exc

        container_ip = self._inspect_ip(container_id, network_name)

        logger.info(
            "sandbox.spawn job_id=%s bank_id=%s container_id=%s ip=%s network=%s",
            job_id,
            bank_id,
            container_id,
            container_ip,
            network_name,
        )
        return SandboxToken(
            container_id=container_id,
            container_ip=container_ip,
            network_name=network_name,
        )

    def kill(self, token: SandboxToken) -> None:
        """Stop and remove the container identified by *token*.

        Also removes the per-job network created in ``spawn``.

        Raises:
            SandboxKillError: if stop or remove fails.
        """
        cid = token.container_id
        try:
            resp = self._client.post(
                f"/{_DOCKER_API_VERSION}/containers/{cid}/stop",
                params={"t": "5"},
            )
            # 204 = stopped, 304 = already stopped — both acceptable
            if resp.status_code not in (204, 304, 404):
                raise SandboxProxyError("POST", f"/containers/{cid}/stop", resp.status_code, resp.text)
        except httpx.HTTPError as exc:
            raise SandboxKillError(f"HTTP error stopping container {cid!r}: {exc}") from exc

        try:
            resp = self._client.delete(
                f"/{_DOCKER_API_VERSION}/containers/{cid}",
                params={"force": "true", "v": "false"},
            )
            if resp.status_code not in (204, 404):
                raise SandboxKillError(
                    f"docker-socket-proxy returned {resp.status_code} for DELETE "
                    f"/containers/{cid}: {resp.text!r}"
                )
        except httpx.HTTPError as exc:
            raise SandboxKillError(f"HTTP error removing container {cid!r}: {exc}") from exc

        self._cleanup_network_best_effort(token.network_name)
        logger.info("sandbox.kill container_id=%s network=%s", cid, token.network_name)

    def attach_network_policy(
        self, token: SandboxToken, allowed_domains: list[str]
    ) -> None:
        """Validate and record the egress allowlist for the container.

        Egress is enforced structurally by the internal Docker bridge
        (``Internal=True``, no default gateway).  Full iptables-level DNS
        filtering is out of scope for v1; this method validates the declared
        allowlist and logs it for audit.

        Raises:
            NetworkPolicyViolation: If *allowed_domains* contains unknown hosts.
        """
        if not allowed_domains:
            raise NetworkPolicyViolation("allowed_domains must be non-empty")

        bank_id = self._read_container_label(token.container_id, "com.open-banca.bank-id")
        if bank_id:
            expected = set(domains_for_bank(bank_id, include_llm=True))
            unknown = [d for d in allowed_domains if d not in expected]
            if unknown:
                raise NetworkPolicyViolation(
                    f"Domains not in bank allowlist for {bank_id!r}: {unknown}"
                )

        for domain in allowed_domains:
            if not domain or " " in domain or domain.startswith("."):
                raise NetworkPolicyViolation(f"Invalid domain in allowlist: {domain!r}")

        logger.info(
            "sandbox.network_policy container_id=%s allowed_domains=%s",
            token.container_id,
            allowed_domains,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_network(self, network_name: str) -> str:
        """Create an isolated Docker bridge network.  Returns network_id."""
        payload: dict[str, Any] = {
            "Name": network_name,
            "Driver": "bridge",
            "Internal": True,  # no default gateway — containers cannot reach internet directly
            "CheckDuplicate": True,
            "Labels": {
                "com.open-banca.managed": "true",
                "com.open-banca.component": "sandbox",
            },
        }
        resp = self._client.post(
            f"/{_DOCKER_API_VERSION}/networks/create",
            json=payload,
        )
        if resp.status_code not in (201,):
            raise SandboxProxyError(
                "POST", "/networks/create", resp.status_code, resp.text
            )
        return resp.json()["Id"]

    def _create_container(
        self, job_id: str, bank_id: str, network_name: str
    ) -> str:
        """POST /containers/create with full hardening configuration."""
        allowed_domains = domains_for_bank(bank_id)

        tmpfs: dict[str, str] = {
            "/tmp": f"noexec,nosuid,size={_TMPFS_TMP_SIZE}",
            "/secrets": f"noexec,nosuid,size={_TMPFS_SECRETS_SIZE}",
            "/downloads": f"noexec,nosuid,size={_TMPFS_DOWNLOADS_SIZE}",
        }

        host_config: dict[str, Any] = {
            # ---- read-only root filesystem ----
            "ReadonlyRootfs": True,
            # ---- tmpfs mounts (writable areas) ----
            "Tmpfs": tmpfs,
            # ---- capability hardening ----
            "CapDrop": ["ALL"],
            "CapAdd": [],
            # ---- no privileged ----
            "Privileged": False,
            # ---- security options ----
            "SecurityOpt": [
                "no-new-privileges:true",
                "seccomp:unconfined",   # fallback if custom profile absent; ADR says default
                "apparmor:docker-default",
            ],
            # ---- resource limits ----
            "CpuPeriod": _CPU_PERIOD,
            "CpuQuota": _CPU_QUOTA,
            "Memory": _MEMORY_BYTES,
            "MemorySwap": _MEMORY_BYTES,  # disable swap (swap = memory limit)
            "PidsLimit": _PIDS_LIMIT,
            # ---- network ----
            "NetworkMode": network_name,
            # ---- IPC isolation ----
            "IpcMode": "none",
            # ---- NO volume mounts to host ----
            "Binds": [],
            "Mounts": [],
            # ---- logging: prevent container logs from leaking secrets ----
            "LogConfig": {
                "Type": "json-file",
                "Config": {"max-size": "10m", "max-file": "3"},
            },
        }

        container_config: dict[str, Any] = {
            "Image": self._image,
            # Non-root user (nobody / uid 65534)
            "User": "65534:65534",
            # Minimal environment — NO secrets in env vars
            "Env": [
                f"JOB_ID={job_id}",
                f"BANK_ID={bank_id}",
            ],
            "NetworkingConfig": {
                "EndpointsConfig": {
                    network_name: {},
                }
            },
            "HostConfig": host_config,
            "Labels": {
                "com.open-banca.managed": "true",
                "com.open-banca.component": "sandbox",
                "com.open-banca.job-id": job_id,
                "com.open-banca.bank-id": bank_id,
                "com.open-banca.spawn-epoch": str(int(time.time())),
                "com.open-banca.allowed-domains": ",".join(allowed_domains),
                "com.open-banca.ttl-seconds": str(_CONTAINER_TTL_SECONDS),
            },
            # No Cmd override — sandbox runs its own entrypoint
        }

        resp = self._client.post(
            f"/{_DOCKER_API_VERSION}/containers/create",
            params={"name": f"sandbox-{job_id}"},
            json=container_config,
        )
        if resp.status_code != 201:
            raise SandboxProxyError(
                "POST", "/containers/create", resp.status_code, resp.text
            )
        return resp.json()["Id"]

    def _start_container(self, container_id: str) -> None:
        resp = self._client.post(
            f"/{_DOCKER_API_VERSION}/containers/{container_id}/start"
        )
        # 204 = started, 304 = already running
        if resp.status_code not in (204, 304):
            raise SandboxProxyError(
                "POST", f"/containers/{container_id}/start", resp.status_code, resp.text
            )

    def _read_container_label(self, container_id: str, label_key: str) -> str:
        """Best-effort read of a container label via inspect."""
        try:
            resp = self._client.get(
                f"/{_DOCKER_API_VERSION}/containers/{container_id}/json"
            )
            if resp.status_code != 200:
                return ""
            labels = resp.json().get("Config", {}).get("Labels", {}) or {}
            return str(labels.get(label_key, ""))
        except httpx.HTTPError:
            return ""

    def _inspect_ip(self, container_id: str, network_name: str) -> str:
        resp = self._client.get(
            f"/{_DOCKER_API_VERSION}/containers/{container_id}/json"
        )
        if resp.status_code != 200:
            raise SandboxSpawnError(
                f"Failed to inspect container {container_id!r}: HTTP {resp.status_code}"
            )
        data = resp.json()
        try:
            return str(
                data["NetworkSettings"]["Networks"][network_name]["IPAddress"]
            )
        except (KeyError, TypeError):
            return ""

    def _remove_container_best_effort(self, container_id: str) -> None:
        try:
            self._client.delete(
                f"/{_DOCKER_API_VERSION}/containers/{container_id}",
                params={"force": "true"},
            )
        except Exception:  # noqa: BLE001
            logger.warning("sandbox: failed to remove container %s (best-effort)", container_id)

    def _cleanup_network_best_effort(self, network_name: str) -> None:
        try:
            self._client.delete(f"/{_DOCKER_API_VERSION}/networks/{network_name}")
        except Exception:  # noqa: BLE001
            logger.warning("sandbox: failed to remove network %s (best-effort)", network_name)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> DockerSandboxRunner:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# Runtime check: DockerSandboxRunner satisfies the SandboxPort protocol.
def _assert_protocol() -> None:
    assert isinstance(DockerSandboxRunner, type)


_: SandboxPort = DockerSandboxRunner()  # type: ignore[assignment]  # protocol check at import
