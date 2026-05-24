"""Tests for OpenAPI 3.1 spec generation."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_openapi_version_is_31(app: object) -> None:
    """The generated OpenAPI spec must declare openapi: 3.1.x."""
    assert isinstance(app, FastAPI)
    spec = app.openapi()
    openapi_version: str = spec.get("openapi", "")
    assert openapi_version.startswith("3.1"), f"Expected OpenAPI 3.1.x, got '{openapi_version}'"


def test_openapi_endpoint_accessible(client: TestClient) -> None:
    """GET /openapi.json must return 200."""
    response = client.get("/openapi.json")
    assert response.status_code == 200


def test_openapi_has_all_paths(app: object) -> None:
    """OpenAPI spec must document all public endpoints."""
    assert isinstance(app, FastAPI)
    spec = app.openapi()
    paths = spec.get("paths", {})

    expected_paths = [
        "/health",
        "/healthz",
        "/readyz",
        "/time",
        "/scrape",
        "/credentials",
        "/jobs/{job_id}",
        "/jobs/{job_id}/result",
        "/jobs/{job_id}/otp-confirmed",
        "/jobs/{job_id}/cancel",
        "/maps/{bank}/proposals/{proposal_id}/approve",
        "/maps/{bank}/proposals/{proposal_id}/reject",
        "/banks",
        "/accounts",
        "/webhooks/test",
    ]

    for expected in expected_paths:
        assert expected in paths, f"Path '{expected}' missing from OpenAPI spec"


def test_docs_endpoint_accessible(client: TestClient) -> None:
    """GET /docs must return 200 (Swagger UI)."""
    response = client.get("/docs")
    assert response.status_code == 200
