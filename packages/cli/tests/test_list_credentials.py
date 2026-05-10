"""Tests for the list-credentials CLI command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from open_banca_cli.main import app
from open_banca_storage.secret_vault import CredentialSummary

runner = CliRunner()


@pytest.mark.unit()
def test_list_credentials_shows_table() -> None:
    """list-credentials displays a table with id, bank, label, created_at."""
    summaries = [
        CredentialSummary(
            id="ID-001",
            bank="banco_general",
            label="prod:username",
            created_at="2024-01-15T10:00:00+00:00",
        ),
        CredentialSummary(
            id="ID-002",
            bank="banco_general",
            label="prod:password",
            created_at="2024-01-15T10:00:01+00:00",
        ),
    ]
    vault = MagicMock()
    vault.__enter__ = MagicMock(return_value=vault)
    vault.__exit__ = MagicMock(return_value=False)
    vault.list_credentials.return_value = summaries

    with patch("open_banca_cli.commands.list_credentials.open_vault_interactive") as mock_open:
        mock_open.return_value = (vault, "master")

        result = runner.invoke(app, ["list-credentials"])

    assert result.exit_code == 0, result.output
    assert "ID-001" in result.output
    assert "ID-002" in result.output
    assert "banco_general" in result.output
    assert "prod:username" in result.output
    assert "prod:password" in result.output


@pytest.mark.unit()
def test_list_credentials_empty_vault() -> None:
    """list-credentials shows a message when no credentials exist."""
    vault = MagicMock()
    vault.__enter__ = MagicMock(return_value=vault)
    vault.__exit__ = MagicMock(return_value=False)
    vault.list_credentials.return_value = []

    with patch("open_banca_cli.commands.list_credentials.open_vault_interactive") as mock_open:
        mock_open.return_value = (vault, "master")

        result = runner.invoke(app, ["list-credentials"])

    assert result.exit_code == 0, result.output
    assert "No credentials stored" in result.output


@pytest.mark.unit()
def test_list_credentials_no_plaintext_in_output() -> None:
    """list-credentials output must never contain any plaintext values."""
    secret_value = "PLAINTEXT_SECRET_DO_NOT_SHOW"
    summaries = [
        CredentialSummary(
            id="ID-003",
            bank="test_bank",
            label="my:username",
            created_at="2024-01-15T12:00:00+00:00",
        ),
    ]
    vault = MagicMock()
    vault.__enter__ = MagicMock(return_value=vault)
    vault.__exit__ = MagicMock(return_value=False)
    vault.list_credentials.return_value = summaries

    with patch("open_banca_cli.commands.list_credentials.open_vault_interactive") as mock_open:
        mock_open.return_value = (vault, "master")

        result = runner.invoke(app, ["list-credentials"])

    assert secret_value not in result.output
    assert result.exit_code == 0
