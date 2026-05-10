"""Tests for /webhooks endpoints: test, DLQ list, DLQ replay."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# ── POST /webhooks/test ────────────────────────────────────────────────────────

def test_webhooks_test_requires_auth(client: TestClient) -> None:
    """POST /webhooks/test must require Bearer token."""
    response = client.post("/webhooks/test", json={"event_type": "job.completed"})
    assert response.status_code == 401


def test_webhooks_test_503_no_target_url(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /webhooks/test returns 503 if target URL is not set."""
    monkeypatch.delenv("OPEN_BANCA_WEBHOOK_TARGET_URL", raising=False)
    response = client.post(
        "/webhooks/test",
        json={"event_type": "job.completed"},
        headers=auth_headers,
    )
    assert response.status_code == 503


def test_webhooks_test_invalid_event_type(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /webhooks/test returns 400 for invalid event_type."""
    monkeypatch.setenv("OPEN_BANCA_WEBHOOK_TARGET_URL", "https://example.com/webhook")
    response = client.post(
        "/webhooks/test",
        json={"event_type": "invalid.type"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "Invalid event_type" in response.json()["detail"]


def test_webhooks_test_enqueues_event(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /webhooks/test enqueues a real webhook event."""
    monkeypatch.setenv("OPEN_BANCA_WEBHOOK_TARGET_URL", "https://example.com/webhook")
    monkeypatch.setenv("OPEN_BANCA_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("OPEN_BANCA_WEBHOOK_DB_PATH", ":memory:")

    response = client.post(
        "/webhooks/test",
        json={"event_type": "job.completed"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["enqueued"] is True
    assert data["outbox_id"] is not None


@pytest.mark.parametrize(
    "event_type",
    [
        "job.created",
        "job.otp_required",
        "job.progress",
        "job.completed",
        "job.failed",
        "job.remap_proposed",
        "job.human_required",
    ],
)
def test_webhooks_test_all_7_event_types(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
) -> None:
    """POST /webhooks/test accepts all 7 event types."""
    monkeypatch.setenv("OPEN_BANCA_WEBHOOK_TARGET_URL", "https://example.com/webhook")
    monkeypatch.setenv("OPEN_BANCA_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("OPEN_BANCA_WEBHOOK_DB_PATH", ":memory:")

    response = client.post(
        "/webhooks/test",
        json={"event_type": event_type},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["enqueued"] is True


# ── GET /webhooks/dlq ──────────────────────────────────────────────────────────

def test_webhooks_dlq_requires_auth(client: TestClient) -> None:
    """GET /webhooks/dlq must require Bearer token."""
    response = client.get("/webhooks/dlq")
    assert response.status_code == 401


def test_webhooks_dlq_returns_list(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /webhooks/dlq returns a list (may be empty)."""
    monkeypatch.setenv("OPEN_BANCA_WEBHOOK_TARGET_URL", "https://example.com/webhook")
    monkeypatch.setenv("OPEN_BANCA_WEBHOOK_DB_PATH", ":memory:")

    response = client.get("/webhooks/dlq", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "count" in data
    assert isinstance(data["items"], list)
    assert data["count"] == len(data["items"])


# ── POST /webhooks/dlq/{id}/replay ─────────────────────────────────────────────

def test_webhooks_dlq_replay_requires_auth(client: TestClient) -> None:
    """POST /webhooks/dlq/{id}/replay must require Bearer token."""
    response = client.post("/webhooks/dlq/some-id/replay")
    assert response.status_code == 401


def test_webhooks_dlq_replay_not_found(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /webhooks/dlq/{id}/replay returns 404 for unknown IDs."""
    monkeypatch.setenv("OPEN_BANCA_WEBHOOK_TARGET_URL", "https://example.com/webhook")
    monkeypatch.setenv("OPEN_BANCA_WEBHOOK_DB_PATH", ":memory:")

    response = client.post(
        "/webhooks/dlq/nonexistent-id/replay",
        headers=auth_headers,
    )
    assert response.status_code == 404
