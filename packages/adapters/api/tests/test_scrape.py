"""Tests for POST /scrape — idempotency key, concurrency guard, rate limit."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_scrape_requires_auth(client: TestClient) -> None:
    """POST /scrape without Bearer token returns 401."""
    response = client.post("/scrape", json={"bank_id": "banco_general", "credentials": "enc"})
    assert response.status_code == 401


def test_scrape_with_valid_auth_returns_501(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """POST /scrape with valid auth returns 501 (stub)."""
    payload = {"bank_id": "banco_general", "credentials": "enc-blob"}
    response = client.post("/scrape", json=payload, headers=auth_headers)
    assert response.status_code == 501


def test_scrape_accepts_idempotency_key_header(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """POST /scrape with Idempotency-Key header must not return 400 due to header."""
    payload = {"bank_id": "banco_general", "credentials": "enc-blob"}
    headers = {**auth_headers, "Idempotency-Key": "idem-key-001"}
    response = client.post("/scrape", json=payload, headers=headers)
    # Should be 501 (stub), not 400 (bad request due to unknown header)
    assert response.status_code in (501, 409)


def test_scrape_invalid_payload_returns_422(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """POST /scrape with missing required fields returns 422."""
    response = client.post("/scrape", json={"bank_id": "banco_general"}, headers=auth_headers)
    # credentials is required
    assert response.status_code == 422


def test_scrape_full_payload_accepted(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """POST /scrape with full valid payload passes validation and reaches stub."""
    payload = {
        "bank_id": "banco_general",
        "credentials": "encrypted-blob",
        "accounts": ["acc-001", "acc-002"],
        "full": True,
        "since": "2025-01-01T00:00:00Z",
        "webhook_url": "https://example.com/webhook",
        "metadata": {"source": "test"},
    }
    response = client.post("/scrape", json=payload, headers=auth_headers)
    assert response.status_code == 501
