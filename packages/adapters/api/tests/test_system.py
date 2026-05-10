"""Tests for /health and /time system endpoints."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient


def test_health_returns_200(client: TestClient) -> None:
    """GET /health must return 200 without auth."""
    response = client.get("/health")
    assert response.status_code == 200


def test_health_body(client: TestClient) -> None:
    """GET /health body must have status=ok."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "version" in body


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
