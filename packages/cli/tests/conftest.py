"""Shared pytest fixtures for CLI tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from open_banca_domain.entities.credential import Credential
from open_banca_storage.secret_vault import CredentialSummary


@pytest.fixture()
def mock_credential() -> Credential:
    """A sample Credential entity for assertions."""
    return Credential(
        id="cred-id-001",
        bank="banco_general",
        credential_ref="ref-001",
        label="prod:username",
    )


@pytest.fixture()
def mock_credential_password() -> Credential:
    """A sample Credential entity for password field."""
    return Credential(
        id="cred-id-002",
        bank="banco_general",
        credential_ref="ref-002",
        label="prod:password",
    )


@pytest.fixture()
def mock_summaries() -> list[CredentialSummary]:
    """Sample credential summaries for list tests."""
    return [
        CredentialSummary(
            id="cred-id-001",
            bank="banco_general",
            label="prod:username",
            created_at="2024-01-15T10:00:00+00:00",
        ),
        CredentialSummary(
            id="cred-id-002",
            bank="banco_general",
            label="prod:password",
            created_at="2024-01-15T10:00:01+00:00",
        ),
    ]


@pytest.fixture()
def mock_vault(
    mock_credential: Credential,
    mock_credential_password: Credential,
    mock_summaries: list[CredentialSummary],
) -> MagicMock:
    """A MagicMock SecretVault with pre-configured return values."""
    vault = MagicMock()
    vault.__enter__ = MagicMock(return_value=vault)
    vault.__exit__ = MagicMock(return_value=False)
    vault.store_credential.side_effect = [mock_credential, mock_credential_password]
    vault.list_credentials.return_value = mock_summaries
    vault.rotate_master.return_value = None
    return vault
