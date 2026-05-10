"""Unit tests for SandboxGarbageCollector."""
from __future__ import annotations

import time

import httpx
import pytest
import respx

from open_banca_sandbox.cleanup import (
    _COMPONENT_LABEL,
    _JOB_ID_LABEL,
    _SPAWN_EPOCH_LABEL,
    SandboxGarbageCollector,
)
from open_banca_sandbox.runner import _DOCKER_API_VERSION, DockerSandboxRunner

_PROXY_URL = "http://docker-socket-proxy:2375"
_LIST_URL = f"{_PROXY_URL}/{_DOCKER_API_VERSION}/containers/json"


def _make_container(
    cid: str,
    job_id: str,
    age_seconds: int = 0,
    component: str = "sandbox",
) -> dict:  # type: ignore[type-arg]
    now = int(time.time())
    spawn_epoch = now - age_seconds
    return {
        "Id": cid,
        "Labels": {
            "com.open-banca.managed": "true",
            _COMPONENT_LABEL: component,
            _JOB_ID_LABEL: job_id,
            _SPAWN_EPOCH_LABEL: str(spawn_epoch),
        },
    }


@pytest.fixture()
def runner() -> DockerSandboxRunner:
    return DockerSandboxRunner(proxy_url=_PROXY_URL)


def test_gc_kills_orphan(runner: DockerSandboxRunner) -> None:
    """Containers whose job_id is not in active_job_ids must be killed."""
    cid = "orphan-container"
    with respx.mock(base_url=_PROXY_URL, assert_all_called=False) as router:
        router.get(f"/{_DOCKER_API_VERSION}/containers/json").mock(
            return_value=httpx.Response(
                200,
                json=[_make_container(cid, "orphan-job", age_seconds=10)],
            )
        )
        delete_route = router.delete(
            f"/{_DOCKER_API_VERSION}/containers/{cid}"
        ).mock(return_value=httpx.Response(204))

        gc = SandboxGarbageCollector(runner, max_age_seconds=3600)
        killed = gc.collect(active_job_ids={"active-job"})

    assert cid in killed
    assert delete_route.called


def test_gc_kills_expired(runner: DockerSandboxRunner) -> None:
    """Containers older than max_age_seconds must be killed even if job is active."""
    cid = "expired-container"
    with respx.mock(base_url=_PROXY_URL, assert_all_called=False) as router:
        router.get(f"/{_DOCKER_API_VERSION}/containers/json").mock(
            return_value=httpx.Response(
                200,
                json=[_make_container(cid, "active-job", age_seconds=400)],
            )
        )
        delete_route = router.delete(
            f"/{_DOCKER_API_VERSION}/containers/{cid}"
        ).mock(return_value=httpx.Response(204))

        gc = SandboxGarbageCollector(runner, max_age_seconds=360)
        killed = gc.collect(active_job_ids={"active-job"})

    assert cid in killed
    assert delete_route.called


def test_gc_spares_active_fresh(runner: DockerSandboxRunner) -> None:
    """Active, non-expired containers must NOT be killed."""
    cid = "fresh-container"
    with respx.mock(base_url=_PROXY_URL, assert_all_called=False) as router:
        router.get(f"/{_DOCKER_API_VERSION}/containers/json").mock(
            return_value=httpx.Response(
                200,
                json=[_make_container(cid, "active-job", age_seconds=10)],
            )
        )
        delete_route = router.delete(
            f"/{_DOCKER_API_VERSION}/containers/{cid}"
        ).mock(return_value=httpx.Response(204))

        gc = SandboxGarbageCollector(runner, max_age_seconds=360)
        killed = gc.collect(active_job_ids={"active-job"})

    assert cid not in killed
    assert not delete_route.called


def test_gc_filters_non_sandbox_labels(runner: DockerSandboxRunner) -> None:
    """Containers with component != 'sandbox' must be ignored."""
    cid = "non-sandbox-container"
    with respx.mock(base_url=_PROXY_URL, assert_all_called=False) as router:
        router.get(f"/{_DOCKER_API_VERSION}/containers/json").mock(
            return_value=httpx.Response(
                200,
                json=[_make_container(cid, "some-job", component="temporal-worker")],
            )
        )
        delete_route = router.delete(
            f"/{_DOCKER_API_VERSION}/containers/{cid}"
        ).mock(return_value=httpx.Response(204))

        gc = SandboxGarbageCollector(runner, max_age_seconds=360)
        killed = gc.collect(active_job_ids=set())

    assert cid not in killed
    assert not delete_route.called


def test_gc_handles_empty_list(runner: DockerSandboxRunner) -> None:
    """No containers returned → no kills, no errors."""
    with respx.mock(base_url=_PROXY_URL, assert_all_called=False) as router:
        router.get(f"/{_DOCKER_API_VERSION}/containers/json").mock(
            return_value=httpx.Response(200, json=[])
        )
        gc = SandboxGarbageCollector(runner, max_age_seconds=360)
        killed = gc.collect(active_job_ids={"job-a"})

    assert killed == []


def test_gc_handles_proxy_error(runner: DockerSandboxRunner) -> None:
    """HTTP error from proxy during list must not propagate — returns empty list."""
    with respx.mock(base_url=_PROXY_URL, assert_all_called=False) as router:
        router.get(f"/{_DOCKER_API_VERSION}/containers/json").mock(
            return_value=httpx.Response(500, text="proxy error")
        )
        gc = SandboxGarbageCollector(runner, max_age_seconds=360)
        killed = gc.collect()

    assert killed == []
