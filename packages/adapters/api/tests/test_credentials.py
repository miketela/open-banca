"""Tests for POST /credentials — auth, validation, vault storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from open_banca_api.dependencies import get_secret_vault
from open_banca_domain.entities.credential import Credential


@dataclass
class _FakeVault:
    """In-memory vault recording store_credential calls."""

    calls: list[tuple[str, str, str]]

    def store_credential(self, bank: str, plaintext: str, label: str) -> Credential:
        self.calls.append((bank, plaintext, label))
        idx = len(self.calls)
        return Credential(
            id=f"cred-{idx}",
            bank=bank,
            credential_ref=f"ref-{idx}",
            label=label,
        )


@pytest.fixture
def vault_client(app: object) -> tuple[TestClient, _FakeVault]:
    """TestClient with get_secret_vault overridden by a fake vault."""
    fake = _FakeVault(calls=[])
    app.dependency_overrides[get_secret_vault] = lambda: fake  # type: ignore[attr-defined, union-attr]
    client = TestClient(app, raise_server_exceptions=True)  # type: ignore[arg-type]
    yield client, fake
    app.dependency_overrides.pop(get_secret_vault, None)  # type: ignore[attr-defined, union-attr]


def test_credentials_requires_auth(client: TestClient) -> None:
    """POST /credentials without Bearer token returns 401."""
    payload = {
        "bank_id": "banco_general",
        "username": "user",
        "password": "pass",
    }
    response = client.post("/credentials", json=payload)
    assert response.status_code == 401


def test_credentials_stores_username_and_password(
    vault_client: tuple[TestClient, _FakeVault],
    auth_headers: dict[str, str],
) -> None:
    """POST /credentials stores username + password and returns credential_ref."""
    client, fake = vault_client
    payload = {
        "bank_id": "banco_general",
        "username": "myuser",
        "password": "mypass",
    }
    response = client.post("/credentials", json=payload, headers=auth_headers)

    assert response.status_code == 201
    data = response.json()
    assert data["bank_id"] == "banco_general"
    assert data["credential_ref"] == "banco_general"
    assert len(data["stored"]) == 2
    assert fake.calls[0] == ("banco_general", "myuser", "banco_general:username")
    assert fake.calls[1] == ("banco_general", "mypass", "banco_general:password")


def test_credentials_custom_label_and_pin(
    vault_client: tuple[TestClient, _FakeVault],
    auth_headers: dict[str, str],
) -> None:
    """POST /credentials with label and pin stores three rows."""
    client, fake = vault_client
    payload = {
        "bank_id": "banco_general",
        "username": "u",
        "password": "p",
        "pin": "1234",
        "label": "prod",
    }
    response = client.post("/credentials", json=payload, headers=auth_headers)

    assert response.status_code == 201
    data = response.json()
    assert data["credential_ref"] == "prod"
    assert len(data["stored"]) == 3
    assert fake.calls[2] == ("banco_general", "1234", "prod:pin")


def test_credentials_invalid_payload_returns_422(
    vault_client: tuple[TestClient, _FakeVault],
    auth_headers: dict[str, str],
) -> None:
    """POST /credentials with missing password returns 422."""
    client, _fake = vault_client
    response = client.post(
        "/credentials",
        json={"bank_id": "banco_general", "username": "only-user"},
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_credentials_response_never_contains_plaintext(
    vault_client: tuple[TestClient, _FakeVault],
    auth_headers: dict[str, str],
) -> None:
    """Response body must not echo username/password."""
    client, _fake = vault_client
    payload = {
        "bank_id": "banco_general",
        "username": "secret-user-xyz",
        "password": "secret-pass-abc",
    }
    response = client.post("/credentials", json=payload, headers=auth_headers)
    body = response.text
    assert "secret-user-xyz" not in body
    assert "secret-pass-abc" not in body
