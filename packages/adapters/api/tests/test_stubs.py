"""Tests for 501 stub endpoints — verify they return the expected status code and message."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_STUB_ENDPOINTS = [
    ("POST", "/scrape", {"bank_id": "banco_general", "credentials": "enc-blob"}),
    ("GET", "/jobs/abc123", None),
    ("GET", "/jobs/abc123/result", None),
    ("POST", "/jobs/abc123/otp-confirmed", None),
    ("POST", "/jobs/abc123/cancel", None),
    ("POST", "/maps/banco_general/proposals/prop-1/approve", None),
    ("POST", "/maps/banco_general/proposals/prop-1/reject", None),
    ("GET", "/banks", None),
    ("GET", "/accounts", None),
    # NOTE: /webhooks/test is implemented (task-25) — removed from stub list.
]


@pytest.mark.parametrize("method,path,body", _STUB_ENDPOINTS)
def test_stub_returns_501(
    client: TestClient,
    auth_headers: dict[str, str],
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    """All stub endpoints must return HTTP 501 when called with valid auth."""
    kwargs: dict[str, object] = {"headers": auth_headers}
    if body is not None:
        kwargs["json"] = body
    response = client.request(method, path, **kwargs)  # type: ignore[arg-type]
    assert response.status_code == 501, (
        f"{method} {path} returned {response.status_code}, expected 501. Body: {response.text}"
    )


@pytest.mark.parametrize("method,path,body", _STUB_ENDPOINTS)
def test_stub_detail_message(
    client: TestClient,
    auth_headers: dict[str, str],
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    """501 responses must include the canonical 'Not implemented' message."""
    kwargs: dict[str, object] = {"headers": auth_headers}
    if body is not None:
        kwargs["json"] = body
    response = client.request(method, path, **kwargs)  # type: ignore[arg-type]
    assert response.status_code == 501
    detail = response.json().get("detail", "")
    assert "Not implemented" in str(detail)
