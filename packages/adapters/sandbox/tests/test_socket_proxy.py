"""Smoke tests para docker-socket-proxy hardening (REQ-018, P0).

Verifica que el proxy bloquea endpoints prohibidos (403) y permite los
necesarios para el spawn del sandbox container.

Gate de ejecución: variable de entorno OPEN_BANCA_DOCKER_INTEGRATION=1.
Sin esa variable los tests se marcan como skip para que CI genérico no
requiera un Docker daemon.

El proxy debe estar corriendo en tcp://localhost:2375 (o en el host
declarado por DOCKER_PROXY_HOST / DOCKER_PROXY_PORT).  En CI local:

    docker compose up -d docker-socket-proxy
    OPEN_BANCA_DOCKER_INTEGRATION=1 uv run pytest \\
        packages/adapters/sandbox/tests/test_socket_proxy.py -v

Threat model: T14 (docker-socket-proxy mal configurado = game-over silencioso).
ADR: 0009-docker-sandbox-per-job.md §Sandbox manager y /var/run/docker.sock.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import httpx
import pytest

# ---------------------------------------------------------------------------
# Configuración del proxy
# ---------------------------------------------------------------------------
PROXY_HOST = os.getenv("DOCKER_PROXY_HOST", "localhost")
PROXY_PORT = int(os.getenv("DOCKER_PROXY_PORT", "2375"))
PROXY_BASE_URL = f"http://{PROXY_HOST}:{PROXY_PORT}"

# API version usada en los tests — debe coincidir con la que usa el worker
API_VERSION = "v1.43"

# Env-gate: los tests solo corren si el operador declara integración con Docker
DOCKER_INTEGRATION = os.getenv("OPEN_BANCA_DOCKER_INTEGRATION", "0") == "1"

skip_without_docker = pytest.mark.skipif(
    not DOCKER_INTEGRATION,
    reason=(
        "Tests de integración con docker-socket-proxy deshabilitados. "
        "Exporta OPEN_BANCA_DOCKER_INTEGRATION=1 para ejecutarlos."
    ),
)

# ---------------------------------------------------------------------------
# Fixture: cliente HTTP reutilizable
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def proxy_client() -> Generator[httpx.Client, None, None]:
    """Cliente HTTP apuntando al docker-socket-proxy."""
    with httpx.Client(base_url=PROXY_BASE_URL, timeout=10.0) as client:
        yield client


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _fake_container_id() -> str:
    """ID de container ficticio para tests de endpoints que requieren {id}."""
    return "aaaaaaaaaaaabbbbbbbbbbbbccccccccccccdddddddddddd"


# ===========================================================================
# ENDPOINTS PROHIBIDOS — deben retornar 403
# ===========================================================================


@skip_without_docker
class TestProhibitedEndpoints:
    """El proxy DEBE retornar 403 en estos endpoints.

    Cualquier 2xx aquí es un fallo de seguridad crítico (T14).
    """

    def test_exec_create_returns_403(self, proxy_client: httpx.Client) -> None:
        """POST /exec — superficie RCE principal.

        Un atacante con acceso al proxy que pueda crear execs en cualquier
        container del host (incl. el worker, el API) tiene RCE en el host.
        """
        cid = _fake_container_id()
        resp = proxy_client.post(
            f"/{API_VERSION}/containers/{cid}/exec",
            json={"Cmd": ["id"], "AttachStdout": True},
        )
        assert resp.status_code == 403, (
            f"FALLO SEGURIDAD: POST exec retornó {resp.status_code}, "
            f"esperado 403.  El proxy no está bloqueando EXEC."
        )

    def test_image_build_returns_403(self, proxy_client: httpx.Client) -> None:
        """POST /build — permite inyectar código arbitrario en imágenes del host."""
        resp = proxy_client.post(f"/{API_VERSION}/build")
        assert resp.status_code == 403, (
            f"FALLO SEGURIDAD: POST build retornó {resp.status_code}, "
            f"esperado 403.  El proxy no está bloqueando BUILD."
        )

    def test_secrets_list_returns_403(self, proxy_client: httpx.Client) -> None:
        """GET /secrets — Swarm secrets expuestos = fuga de credenciales."""
        resp = proxy_client.get(f"/{API_VERSION}/secrets")
        assert resp.status_code == 403, (
            f"FALLO SEGURIDAD: GET secrets retornó {resp.status_code}, "
            f"esperado 403.  El proxy no está bloqueando SECRETS."
        )

    def test_services_create_returns_403(self, proxy_client: httpx.Client) -> None:
        """POST /services/create — Swarm service creation = escalación de privilegios."""
        resp = proxy_client.post(
            f"/{API_VERSION}/services/create",
            json={"Name": "evil-service"},
        )
        assert resp.status_code == 403, (
            f"FALLO SEGURIDAD: POST services/create retornó {resp.status_code}, "
            f"esperado 403.  El proxy no está bloqueando SERVICES."
        )

    def test_volumes_create_returns_403(self, proxy_client: httpx.Client) -> None:
        """POST /volumes/create — bind mount del host desde un container = escape.

        Este endpoint es el más crítico junto con /exec para ataques de
        container escape que montan /etc o / del host.
        """
        resp = proxy_client.post(
            f"/{API_VERSION}/volumes/create",
            json={"Name": "evil-vol", "Driver": "local"},
        )
        assert resp.status_code == 403, (
            f"FALLO SEGURIDAD: POST volumes/create retornó {resp.status_code}, "
            f"esperado 403.  El proxy no está bloqueando VOLUMES."
        )

    def test_commit_returns_403(self, proxy_client: httpx.Client) -> None:
        """POST /commit — permite persistir estado comprometido en una imagen."""
        resp = proxy_client.post(f"/{API_VERSION}/commit")
        assert resp.status_code == 403, (
            f"FALLO SEGURIDAD: POST commit retornó {resp.status_code}, "
            f"esperado 403.  El proxy no está bloqueando COMMIT."
        )

    def test_swarm_init_returns_403(self, proxy_client: httpx.Client) -> None:
        """POST /swarm/init — iniciar Swarm mode en el host del operador."""
        resp = proxy_client.post(
            f"/{API_VERSION}/swarm/init",
            json={"ListenAddr": "0.0.0.0:2377"},
        )
        assert resp.status_code == 403, (
            f"FALLO SEGURIDAD: POST swarm/init retornó {resp.status_code}, "
            f"esperado 403.  El proxy no está bloqueando SWARM."
        )

    def test_plugins_install_returns_403(self, proxy_client: httpx.Client) -> None:
        """POST /plugins/pull — instalar plugins = código arbitrario en daemon."""
        resp = proxy_client.post(
            f"/{API_VERSION}/plugins/pull",
            params={"remote": "evil/plugin:latest"},
        )
        assert resp.status_code == 403, (
            f"FALLO SEGURIDAD: POST plugins/pull retornó {resp.status_code}, "
            f"esperado 403.  El proxy no está bloqueando PLUGINS."
        )


# ===========================================================================
# ENDPOINTS PERMITIDOS — deben retornar algo distinto de 403/401/405
# ===========================================================================


@skip_without_docker
class TestAllowedEndpoints:
    """El proxy DEBE permitir estos endpoints para que el worker funcione.

    No requerimos 200 exacto (puede que el daemon retorne 404 si el container
    no existe, o 400 por body inválido), pero jamás debe retornar 403
    (proxy-denied) ni 401 (auth no configurada).
    """

    def test_containers_list_allowed(self, proxy_client: httpx.Client) -> None:
        """GET /containers/json — listar containers activos."""
        resp = proxy_client.get(f"/{API_VERSION}/containers/json")
        assert resp.status_code != 403, (
            f"El proxy bloqueó GET containers/json ({resp.status_code}).  "
            f"CONTAINERS=1 no está activo."
        )
        assert resp.status_code != 401, "El proxy retornó 401 inesperado."

    def test_containers_create_allowed(self, proxy_client: httpx.Client) -> None:
        """POST /containers/create — crear el sandbox container (POST=1 + CONTAINERS=1)."""
        resp = proxy_client.post(
            f"/{API_VERSION}/containers/create",
            params={"name": "open-banca-smoke-test-probe"},
            json={
                "Image": "scratch",  # imagen que no existe — esperamos 404, no 403
                "Cmd": ["true"],
            },
        )
        # 400/404/409 = proxy permitió pero daemon rechazó por otras razones
        # 403 = proxy bloqueó — es el fallo que queremos detectar
        assert resp.status_code != 403, (
            f"El proxy bloqueó POST containers/create ({resp.status_code}).  "
            f"POST=1 no está activo."
        )
        assert resp.status_code != 401, "El proxy retornó 401 inesperado."

    def test_containers_start_allowed(self, proxy_client: httpx.Client) -> None:
        """POST /containers/{id}/start — arrancar el sandbox container."""
        cid = _fake_container_id()
        resp = proxy_client.post(f"/{API_VERSION}/containers/{cid}/start")
        # 404 = container no existe (esperado con fake id), pero no 403
        assert resp.status_code != 403, (
            f"El proxy bloqueó POST containers/start ({resp.status_code}).  "
            f"POST=1 + CONTAINERS=1 no están activos."
        )
        assert resp.status_code != 401, "El proxy retornó 401 inesperado."

    def test_images_list_allowed(self, proxy_client: httpx.Client) -> None:
        """GET /images/json — listar imágenes disponibles en el host."""
        resp = proxy_client.get(f"/{API_VERSION}/images/json")
        assert resp.status_code != 403, (
            f"El proxy bloqueó GET images/json ({resp.status_code}).  "
            f"IMAGES=1 no está activo."
        )
        assert resp.status_code != 401, "El proxy retornó 401 inesperado."

    def test_networks_list_allowed(self, proxy_client: httpx.Client) -> None:
        """GET /networks — listar redes (necesario para crear red efímera por job)."""
        resp = proxy_client.get(f"/{API_VERSION}/networks")
        assert resp.status_code != 403, (
            f"El proxy bloqueó GET networks ({resp.status_code}).  "
            f"NETWORKS=1 no está activo."
        )
        assert resp.status_code != 401, "El proxy retornó 401 inesperado."
