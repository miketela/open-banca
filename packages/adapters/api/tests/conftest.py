"""Shared pytest fixtures for open-banca API tests."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Set required env vars BEFORE importing the app so Settings picks them up
os.environ.setdefault("OPEN_BANCA_API_TOKEN", "test-token-abc123")

from open_banca_api.config import get_settings  # noqa: E402
from open_banca_api.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def app() -> object:
    """Create a fresh app instance for the test session."""
    get_settings.cache_clear()
    return create_app()


@pytest.fixture(scope="session")
def client(app: object) -> TestClient:  # type: ignore[type-arg]
    """TestClient for the full app."""
    return TestClient(app, raise_server_exceptions=True)  # type: ignore[arg-type]


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Valid authorization headers."""
    return {"Authorization": "Bearer test-token-abc123"}
