"""Tests for the register-credentials CLI command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from open_banca_cli.main import app
from open_banca_domain.entities.credential import Credential

runner = CliRunner()

# Patch zeroize_str to a no-op in tests that don't test zeroize behaviour.
# The ctypes.memset approach corrupts CPython's heap during pytest GC teardown
# when it runs on string constants that pytest introspects.
_NOOP_ZEROIZE = patch(
    "open_banca_cli.commands.register_credentials.zeroize_str",
    lambda _s: None,
)

# A bank that has no PIN requirement — avoids a third credential mock.
_BANK_NO_PIN = "caja_de_ahorros"


def _make_vault_mock(credentials: list[Credential]) -> MagicMock:
    """Build a MagicMock vault that returns credentials from store_credential."""
    vault = MagicMock()
    vault.__enter__ = MagicMock(return_value=vault)
    vault.__exit__ = MagicMock(return_value=False)
    vault.store_credential.side_effect = credentials
    return vault


@pytest.mark.unit()
def test_register_credentials_stores_username_and_password() -> None:
    """register-credentials stores two rows: username + password."""
    cred_user = Credential(
        id="id-user", bank=_BANK_NO_PIN, credential_ref="ref-user", label="mylabel:username"
    )
    cred_pass = Credential(
        id="id-pass", bank=_BANK_NO_PIN, credential_ref="ref-pass", label="mylabel:password"
    )
    mock_vault = _make_vault_mock([cred_user, cred_pass])

    with (
        _NOOP_ZEROIZE,
        patch("open_banca_cli.commands.register_credentials.open_vault_interactive") as mock_open,
    ):
        mock_open.return_value = (mock_vault, "master-pass")

        result = runner.invoke(
            app,
            ["register-credentials", "--bank", _BANK_NO_PIN, "--label", "mylabel"],
            # username, password (no confirmation prompt — hide_input only), confirm y
            input="myuser\nmypassword\ny\n",
        )

    assert result.exit_code == 0, result.output
    assert mock_vault.store_credential.call_count == 2
    # Verify username call
    first_call = mock_vault.store_credential.call_args_list[0]
    assert first_call.kwargs["bank"] == _BANK_NO_PIN
    assert first_call.kwargs["label"] == "mylabel:username"
    assert first_call.kwargs["plaintext"] == "myuser"
    # Verify password call
    second_call = mock_vault.store_credential.call_args_list[1]
    assert second_call.kwargs["label"] == "mylabel:password"
    assert second_call.kwargs["plaintext"] == "mypassword"


@pytest.mark.unit()
def test_register_credentials_default_label_is_bank() -> None:
    """When --label is omitted the bank id is used as label prefix."""
    cred_user = Credential(
        id="id-u", bank=_BANK_NO_PIN, credential_ref="ref-u", label=f"{_BANK_NO_PIN}:username"
    )
    cred_pass = Credential(
        id="id-p", bank=_BANK_NO_PIN, credential_ref="ref-p", label=f"{_BANK_NO_PIN}:password"
    )
    mock_vault = _make_vault_mock([cred_user, cred_pass])

    with (
        _NOOP_ZEROIZE,
        patch("open_banca_cli.commands.register_credentials.open_vault_interactive") as mock_open,
    ):
        mock_open.return_value = (mock_vault, "master")

        result = runner.invoke(
            app,
            ["register-credentials", "--bank", _BANK_NO_PIN],
            input="user123\nsecret\ny\n",
        )

    assert result.exit_code == 0, result.output
    calls = mock_vault.store_credential.call_args_list
    labels = [c.kwargs["label"] for c in calls]
    assert f"{_BANK_NO_PIN}:username" in labels
    assert f"{_BANK_NO_PIN}:password" in labels


@pytest.mark.unit()
def test_register_credentials_with_pin_bank() -> None:
    """Banks in the PIN list get a third credential row for pin."""
    cred_user = Credential(
        id="id-u", bank="banistmo", credential_ref="r1", label="banistmo:username"
    )
    cred_pass = Credential(
        id="id-p", bank="banistmo", credential_ref="r2", label="banistmo:password"
    )
    cred_pin = Credential(id="id-pin", bank="banistmo", credential_ref="r3", label="banistmo:pin")
    mock_vault = _make_vault_mock([cred_user, cred_pass, cred_pin])

    with (
        _NOOP_ZEROIZE,
        patch("open_banca_cli.commands.register_credentials.open_vault_interactive") as mock_open,
    ):
        mock_open.return_value = (mock_vault, "master")

        result = runner.invoke(
            app,
            ["register-credentials", "--bank", "banistmo"],
            # user, pass, pin (non-empty), confirm y
            input="user123\nsecret\n1234\ny\n",
        )

    assert result.exit_code == 0, result.output
    calls = mock_vault.store_credential.call_args_list
    labels = [c.kwargs["label"] for c in calls]
    assert "banistmo:username" in labels
    assert "banistmo:password" in labels
    assert "banistmo:pin" in labels


@pytest.mark.unit()
def test_register_credentials_shows_credential_ids_not_plaintext() -> None:
    """Output must contain credential IDs but NOT the plaintext password."""
    cred_user = Credential(
        id="CRED-ID-ALPHA", bank=_BANK_NO_PIN, credential_ref="r1", label="main:username"
    )
    cred_pass = Credential(
        id="CRED-ID-BETA", bank=_BANK_NO_PIN, credential_ref="r2", label="main:password"
    )
    mock_vault = _make_vault_mock([cred_user, cred_pass])

    with (
        _NOOP_ZEROIZE,
        patch("open_banca_cli.commands.register_credentials.open_vault_interactive") as mock_open,
    ):
        mock_open.return_value = (mock_vault, "master")

        result = runner.invoke(
            app,
            ["register-credentials", "--bank", _BANK_NO_PIN, "--label", "main"],
            input="myuser\nSUPER_SECRET_PASS\ny\n",
        )

    assert result.exit_code == 0, result.output
    assert "CRED-ID-ALPHA" in result.output
    assert "CRED-ID-BETA" in result.output
    assert "SUPER_SECRET_PASS" not in result.output


@pytest.mark.unit()
def test_register_credentials_abort_on_no_confirmation() -> None:
    """Answering 'n' to the confirmation prompt aborts without storing."""
    mock_vault = _make_vault_mock([])

    with (
        _NOOP_ZEROIZE,
        patch("open_banca_cli.commands.register_credentials.open_vault_interactive") as mock_open,
    ):
        mock_open.return_value = (mock_vault, "master")

        result = runner.invoke(
            app,
            ["register-credentials", "--bank", _BANK_NO_PIN, "--label", "test"],
            input="user\npass\nn\n",
        )

    assert result.exit_code == 0
    mock_vault.store_credential.assert_not_called()


@pytest.mark.unit()
def test_register_credentials_zeroizes_plaintext() -> None:
    """zeroize_str is called for username and password after store."""
    cred_user = Credential(id="id-u", bank=_BANK_NO_PIN, credential_ref="r1", label="z:username")
    cred_pass = Credential(id="id-p", bank=_BANK_NO_PIN, credential_ref="r2", label="z:password")
    mock_vault = _make_vault_mock([cred_user, cred_pass])

    zeroize_calls: list[str] = []

    def recording_zeroize(s: str) -> None:
        zeroize_calls.append(s)

    with (
        patch("open_banca_cli.commands.register_credentials.open_vault_interactive") as mock_open,
        patch(
            "open_banca_cli.commands.register_credentials.zeroize_str",
            side_effect=recording_zeroize,
        ),
    ):
        mock_open.return_value = (mock_vault, "master")

        result = runner.invoke(
            app,
            ["register-credentials", "--bank", _BANK_NO_PIN, "--label", "z"],
            input="testuser\ntestpass\ny\n",
        )

    assert result.exit_code == 0, result.output
    # Verify zeroize was called with the plaintext values
    assert "testuser" in zeroize_calls
    assert "testpass" in zeroize_calls
