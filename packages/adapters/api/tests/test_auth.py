"""Tests for Bearer token authentication."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# Auth-protected endpoint — any stub endpoint will do
_PROTECTED_PATHS = [
    ("GET", "/jobs/fake-id"),
    ("GET", "/banks"),
    ("GET", "/accounts"),
    ("POST", "/jobs/fake-id/cancel"),
]


@pytest.mark.parametrize("method,path", _PROTECTED_PATHS)
def test_missing_token_returns_401(client: TestClient, method: str, path: str) -> None:
    """Endpoints without Bearer token must return 401."""
    response = client.request(method, path)
    assert response.status_code == 401


@pytest.mark.parametrize("method,path", _PROTECTED_PATHS)
def test_wrong_token_returns_401(client: TestClient, method: str, path: str) -> None:
    """Endpoints with wrong Bearer token must return 401."""
    response = client.request(method, path, headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


@pytest.mark.parametrize("method,path", _PROTECTED_PATHS)
def test_valid_token_not_401(client: TestClient, auth_headers: dict[str, str], method: str, path: str) -> None:
    """Endpoints with correct Bearer token must NOT return 401."""
    response = client.request(method, path, headers=auth_headers)
    assert response.status_code != 401
