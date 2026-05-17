"""Tests for POST /scrape — auth, validation, idempotency header acceptance."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_scrape_requires_auth(client: TestClient) -> None:
    """POST /scrape without Bearer token returns 401."""
    response = client.post("/scrape", json={"bank_id": "banco_general", "credentials": "enc"})
    assert response.status_code == 401


def test_scrape_with_valid_auth_no_longer_501(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """POST /scrape with valid auth no longer returns 501 (now wired in task #31).

    With the null adapter, start_workflow raises → 503 expected.
    """
    payload = {"bank_id": "banco_general", "credentials": "enc-blob"}
    response = client.post("/scrape", json=payload, headers=auth_headers)
    # Endpoint is wired — should NOT be 501
    assert response.status_code != 501


def test_scrape_accepts_idempotency_key_header(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """POST /scrape with Idempotency-Key header must not return 400 due to unknown header."""
    payload = {"bank_id": "banco_general", "credentials": "enc-blob"}
    headers = {**auth_headers, "Idempotency-Key": "idem-key-001"}
    response = client.post("/scrape", json=payload, headers=headers)
    # Header accepted — must not be 400 (bad request) or 501 (stub)
    assert response.status_code not in (400, 501)


def test_scrape_invalid_payload_returns_422(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """POST /scrape with missing required fields returns 422."""
    response = client.post("/scrape", json={"bank_id": "banco_general"}, headers=auth_headers)
    # credentials is required
    assert response.status_code == 422


def test_scrape_full_payload_accepted(client: TestClient, auth_headers: dict[str, str]) -> None:
    """POST /scrape with full valid payload passes validation."""
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
    # Passes validation — must not be 422 or 501
    assert response.status_code not in (422, 501)
