"""Tests for /health, /healthz, /readyz, and /time system endpoints."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from open_banca_api.dependencies import get_storage_connection, get_temporal_client


def test_health_returns_200(client: TestClient) -> None:
    """GET /health must return 200 without auth."""
    response = client.get("/health")
    assert response.status_code == 200


def test_health_body(client: TestClient) -> None:
    """GET /health body must have status=ok."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "version" in body


def test_healthz_returns_200(client: TestClient) -> None:
    """GET /healthz must return 200 without auth."""
    response = client.get("/healthz")
    assert response.status_code == 200


def test_healthz_matches_health(client: TestClient) -> None:
    """GET /healthz must return the same payload as GET /health."""
    health_body = client.get("/health").json()
    healthz_body = client.get("/healthz").json()
    assert healthz_body == health_body


def test_readyz_returns_200_when_database_accessible(client: TestClient) -> None:
    """GET /readyz returns 200 when at least the database check passes."""
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] is True
    assert body["checks"]["temporal"] is False
    assert "version" in body


def test_readyz_returns_503_when_all_checks_fail(app: object) -> None:
    """GET /readyz returns 503 when Temporal and database checks both fail."""
    original_overrides = dict(app.dependency_overrides)  # type: ignore[attr-defined]

    class _FailingTemporalAdapter:
        async def async_check_readiness(self) -> bool:
            return False

    def _failing_storage_connection() -> object:
        class _BrokenConnection:
            def execute(self, _query: str) -> None:
                raise RuntimeError("database unavailable")

        return _BrokenConnection()

    try:
        app.dependency_overrides[get_temporal_client] = lambda: _FailingTemporalAdapter()  # type: ignore[attr-defined]
        app.dependency_overrides[get_storage_connection] = _failing_storage_connection  # type: ignore[attr-defined]

        with TestClient(app, raise_server_exceptions=True) as failing_client:  # type: ignore[arg-type]
            response = failing_client.get("/readyz")
            assert response.status_code == 503
            body = response.json()
            assert body["status"] == "not_ready"
            assert body["checks"] == {"temporal": False, "database": False}
    finally:
        app.dependency_overrides.clear()  # type: ignore[attr-defined]
        app.dependency_overrides.update(original_overrides)  # type: ignore[attr-defined]


def test_readyz_returns_200_when_only_temporal_ready(app: object) -> None:
    """GET /readyz returns 200 when Temporal is ready even if DB is down."""
    original_overrides = dict(app.dependency_overrides)  # type: ignore[attr-defined]

    class _HealthyTemporalAdapter:
        async def async_check_readiness(self) -> bool:
            return True

    def _failing_storage_connection() -> object:
        class _BrokenConnection:
            def execute(self, _query: str) -> None:
                raise RuntimeError("database unavailable")

        return _BrokenConnection()

    try:
        app.dependency_overrides[get_temporal_client] = lambda: _HealthyTemporalAdapter()  # type: ignore[attr-defined]
        app.dependency_overrides[get_storage_connection] = _failing_storage_connection  # type: ignore[attr-defined]

        with TestClient(app, raise_server_exceptions=True) as temporal_client:  # type: ignore[arg-type]
            response = temporal_client.get("/readyz")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "ready"
            assert body["checks"]["temporal"] is True
            assert body["checks"]["database"] is False
    finally:
        app.dependency_overrides.clear()  # type: ignore[attr-defined]
        app.dependency_overrides.update(original_overrides)  # type: ignore[attr-defined]


def test_time_returns_200(client: TestClient) -> None:
    """GET /time must return 200 without auth."""
    response = client.get("/time")
    assert response.status_code == 200


def test_time_is_utc_and_recent(client: TestClient) -> None:
    """GET /time must return a timestamp within 5 seconds of now (REQ-010)."""
    before = datetime.now(UTC)
    body = client.get("/time").json()
    after = datetime.now(UTC)

    assert "iso8601" in body
    assert "utc" in body

    # Parse the returned time and check delta
    returned = datetime.fromisoformat(body["iso8601"])
    assert before <= returned <= after or abs((returned - before).total_seconds()) < 5


def test_time_iso8601_parses(client: TestClient) -> None:
    """GET /time iso8601 field must be parseable as a datetime."""
    body = client.get("/time").json()
    dt = datetime.fromisoformat(body["iso8601"])
    assert dt.tzinfo is not None, "datetime must be timezone-aware"
