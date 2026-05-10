"""Canary test: passwords must NEVER appear in stdout/stderr during register-credentials.

This test is a security gate: if any plaintext value leaks into CLI output
(via a print, logger, exception traceback, or Rich formatting) the test fails.

NOTE: zeroize_str is patched to a no-op here to prevent CPython heap corruption
during pytest GC teardown.  The actual zeroize contract is tested in
test_register_credentials.py::test_register_credentials_zeroizes_plaintext.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from open_banca_cli.main import app
from open_banca_domain.entities.credential import Credential

runner = CliRunner()

_CANARY_USERNAME = "CANARY_USERNAME_SHOULD_NOT_APPEAR"
_CANARY_PASSWORD = "CANARY_PASSWORD_MUST_NOT_APPEAR_IN_OUTPUT"
_CANARY_PIN = "CANARY_PIN_SHOULD_NOT_APPEAR"

_NOOP_ZEROIZE = patch("open_banca_cli.commands.register_credentials.zeroize_str", lambda _s: None)
# Bank without PIN requirement to keep canary input simple.
_BANK_NO_PIN = "caja_de_ahorros"


def _make_vault(creds: list[Credential]) -> MagicMock:
    v = MagicMock()
    v.__enter__ = MagicMock(return_value=v)
    v.__exit__ = MagicMock(return_value=False)
    v.store_credential.side_effect = creds
    return v


@pytest.mark.unit()
def test_register_credentials_canary_no_password_in_stdout() -> None:
    """Canary: plaintext password must not appear anywhere in CLI stdout."""
    cred_user = Credential(id="id-u", bank="banco_general", credential_ref="r1", label="c:username")
    cred_pass = Credential(id="id-p", bank="banco_general", credential_ref="r2", label="c:password")
    vault = _make_vault([cred_user, cred_pass])

    with (
        _NOOP_ZEROIZE,
        patch("open_banca_cli.commands.register_credentials.open_vault_interactive") as mock_open,
    ):
        mock_open.return_value = (vault, "master")

        result = runner.invoke(
            app,
            ["register-credentials", "--bank", _BANK_NO_PIN, "--label", "c"],
            input=(f"{_CANARY_USERNAME}\n{_CANARY_PASSWORD}\ny\n"),
        )

    combined_output = result.output
    # Password must NEVER appear (security invariant).
    assert _CANARY_PASSWORD not in combined_output, (
        f"SECURITY VIOLATION: password appeared in output:\n{combined_output}"
    )
    # Username is intentionally shown in the summary (per spec "showing only username + label").
    # This assertion documents that only the password is the secret to protect.
    assert result.exit_code == 0, result.output


@pytest.mark.unit()
def test_register_credentials_canary_no_password_in_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Canary: plaintext password must not appear in log records."""
    cred_user = Credential(id="id-u2", bank=_BANK_NO_PIN, credential_ref="r1", label="c2:username")
    cred_pass = Credential(id="id-p2", bank=_BANK_NO_PIN, credential_ref="r2", label="c2:password")
    vault = _make_vault([cred_user, cred_pass])

    with (
        caplog.at_level(logging.DEBUG),
        _NOOP_ZEROIZE,
        patch("open_banca_cli.commands.register_credentials.open_vault_interactive") as mock_open,
    ):
        mock_open.return_value = (vault, "master")

        runner.invoke(
            app,
            ["register-credentials", "--bank", _BANK_NO_PIN, "--label", "c2"],
            input=(f"{_CANARY_USERNAME}\n{_CANARY_PASSWORD}\ny\n"),
        )

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert _CANARY_PASSWORD not in log_text, (
        f"SECURITY VIOLATION: password appeared in logs:\n{log_text}"
    )


@pytest.mark.unit()
def test_list_credentials_canary_no_plaintext() -> None:
    """Canary: list-credentials must not expose any plaintext credential values."""
    from open_banca_storage.secret_vault import CredentialSummary

    summaries = [
        CredentialSummary(
            id="ID-SAFE",
            bank="banco_general",
            label="safe:username",
            created_at="2024-01-01T00:00:00+00:00",
        )
    ]
    vault = MagicMock()
    vault.__enter__ = MagicMock(return_value=vault)
    vault.__exit__ = MagicMock(return_value=False)
    vault.list_credentials.return_value = summaries

    with patch("open_banca_cli.commands.list_credentials.open_vault_interactive") as mock_open:
        mock_open.return_value = (vault, "master")

        result = runner.invoke(app, ["list-credentials"])

    assert _CANARY_PASSWORD not in result.output
    assert _CANARY_USERNAME not in result.output
    # Metadata (id, bank, label) is allowed to appear
    assert "ID-SAFE" in result.output
    assert result.exit_code == 0
