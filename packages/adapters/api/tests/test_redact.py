"""Tests for redact middleware — canary values must not leak in responses.

Security rule: CLAUDE.md §Security Critical Rules #1.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


def _make_canary_app(secret: str) -> FastAPI:
    """Create a test app that leaks the canary value in a response."""
    # Set env BEFORE importing config so cache captures it
    os.environ["OPEN_BANCA_API_TOKEN"] = "test-token-abc123"
    os.environ["SECRET_CANARY_VALUE"] = secret

    from open_banca_api.config import get_settings

    get_settings.cache_clear()

    from open_banca_api.main import create_app

    test_app = create_app()

    @test_app.get("/leak-test")
    async def leak() -> JSONResponse:
        """Deliberately leak the secret — middleware should redact it."""
        return JSONResponse({"secret": secret, "other": "data"})

    return test_app


def test_canary_value_redacted_from_response() -> None:
    """SECRET_CANARY_VALUE must be redacted from JSON responses."""
    canary = "super-secret-canary-abc999"
    test_app = _make_canary_app(canary)

    with TestClient(test_app, raise_server_exceptions=True) as c:
        response = c.get("/leak-test")

    assert response.status_code == 200
    body = response.text
    assert canary not in body, f"Canary value '{canary}' leaked in response body"
    assert "[REDACTED]" in body


def test_canary_value_not_in_normal_responses(client: TestClient) -> None:
    """Normal responses must not contain canary value."""
    response = client.get("/health")
    assert response.status_code == 200
    assert "SECRET_CANARY_VALUE" not in response.text


def test_pii_canary_redacted() -> None:
    """PII_CANARY_* env vars must also be redacted from response bodies."""
    pii_value = "john-doe-pii-12345"
    os.environ["PII_CANARY_NAME"] = pii_value
    os.environ["OPEN_BANCA_API_TOKEN"] = "test-token-abc123"
    os.environ["SECRET_CANARY_VALUE"] = ""

    from open_banca_api.config import get_settings

    get_settings.cache_clear()

    from open_banca_api.main import create_app

    test_app = create_app()

    @test_app.get("/pii-leak-test")
    async def pii_leak() -> JSONResponse:
        return JSONResponse({"name": pii_value})

    with TestClient(test_app, raise_server_exceptions=True) as c:
        response = c.get("/pii-leak-test")

    assert response.status_code == 200
    assert pii_value not in response.text, f"PII canary '{pii_value}' leaked"
    assert "[REDACTED]" in response.text

    # Cleanup
    del os.environ["PII_CANARY_NAME"]
    get_settings.cache_clear()
