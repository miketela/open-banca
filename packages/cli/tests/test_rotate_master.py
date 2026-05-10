"""Tests for the rotate-master CLI command."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from open_banca_cli.main import app

runner = CliRunner()

# Patch zeroize_str to a no-op — ctypes.memset on short test strings corrupts
# Click/Typer's ExitStack when strings are interned by CPython.
_NOOP_ZEROIZE = patch(
    "open_banca_cli.commands.rotate_master.zeroize_str",
    lambda _s: None,
)


def _make_fake_db(tmp_path: Path) -> Path:
    """Create a non-empty file to simulate an existing vault DB."""
    db = tmp_path / "test.db"
    db.write_bytes(b"\x00" * 64)  # non-empty file
    return db


@pytest.mark.unit()
def test_rotate_master_calls_vault_rotate(tmp_path: Path) -> None:
    """rotate-master invokes vault.rotate_master() after setting the env var."""
    db_path = _make_fake_db(tmp_path)

    vault = MagicMock()
    vault.__enter__ = MagicMock(return_value=vault)
    vault.__exit__ = MagicMock(return_value=False)
    vault.rotate_master.return_value = None

    with (
        _NOOP_ZEROIZE,
        patch.dict(os.environ, {"OPEN_BANCA_DB_PATH": str(db_path)}, clear=False),
        patch("open_banca_cli.commands.rotate_master.ConnectionPool") as mock_pool_cls,
        patch("open_banca_cli.commands.rotate_master.SecretVault") as mock_vault_cls,
        patch("open_banca_cli.commands.rotate_master.migrate"),
    ):
        mock_pool = MagicMock()
        mock_pool.get.return_value = MagicMock()
        mock_pool_cls.return_value = mock_pool
        mock_vault_cls.return_value = vault

        result = runner.invoke(
            app,
            ["rotate-master"],
            # old pass, new pass, new pass confirm, 'y' to proceed
            input="oldpass\nnewpass123\nnewpass123\ny\n",
        )

    assert result.exit_code == 0, result.output
    vault.rotate_master.assert_called_once()


@pytest.mark.unit()
def test_rotate_master_sets_and_clears_env_var(tmp_path: Path) -> None:
    """OPEN_BANCA_MASTER_PASSPHRASE_NEW is set before and cleared after rotation."""
    db_path = _make_fake_db(tmp_path)
    captured_env_value: list[str] = []

    vault = MagicMock()
    vault.__enter__ = MagicMock(return_value=vault)
    vault.__exit__ = MagicMock(return_value=False)

    def capture_and_rotate() -> None:
        captured_env_value.append(os.environ.get("OPEN_BANCA_MASTER_PASSPHRASE_NEW", ""))

    vault.rotate_master.side_effect = capture_and_rotate

    with (
        _NOOP_ZEROIZE,
        patch.dict(os.environ, {"OPEN_BANCA_DB_PATH": str(db_path)}, clear=False),
        patch("open_banca_cli.commands.rotate_master.ConnectionPool") as mock_pool_cls,
        patch("open_banca_cli.commands.rotate_master.SecretVault") as mock_vault_cls,
        patch("open_banca_cli.commands.rotate_master.migrate"),
    ):
        mock_pool_cls.return_value = MagicMock()
        mock_pool_cls.return_value.get.return_value = MagicMock()
        mock_vault_cls.return_value = vault

        result = runner.invoke(
            app,
            ["rotate-master"],
            input="oldpass\nnewnewpass\nnewnewpass\ny\n",
        )

    assert result.exit_code == 0, result.output
    # The env var should have been set to the new passphrase at rotation time
    assert captured_env_value == ["newnewpass"]
    # And cleared afterwards
    assert os.environ.get("OPEN_BANCA_MASTER_PASSPHRASE_NEW") is None


@pytest.mark.unit()
def test_rotate_master_aborts_on_no_confirmation(tmp_path: Path) -> None:
    """Answering 'n' to the confirmation prompt aborts without rotating."""
    db_path = _make_fake_db(tmp_path)

    vault = MagicMock()

    with (
        _NOOP_ZEROIZE,
        patch.dict(os.environ, {"OPEN_BANCA_DB_PATH": str(db_path)}, clear=False),
        patch("open_banca_cli.commands.rotate_master.ConnectionPool") as mock_pool_cls,
        patch("open_banca_cli.commands.rotate_master.SecretVault") as mock_vault_cls,
        patch("open_banca_cli.commands.rotate_master.migrate"),
    ):
        mock_pool_cls.return_value = MagicMock()
        mock_pool_cls.return_value.get.return_value = MagicMock()
        mock_vault_cls.return_value = vault

        result = runner.invoke(
            app,
            ["rotate-master"],
            input="oldpass\nnewpass123\nnewpass123\nn\n",
        )

    assert result.exit_code == 0
    vault.rotate_master.assert_not_called()


@pytest.mark.unit()
def test_rotate_master_fails_when_vault_not_found(tmp_path: Path) -> None:
    """rotate-master exits with code 1 when the DB does not exist."""
    missing_db = tmp_path / "nonexistent.db"

    with patch.dict(os.environ, {"OPEN_BANCA_DB_PATH": str(missing_db)}, clear=False):
        result = runner.invoke(
            app,
            ["rotate-master"],
            input="oldpass\nnewpass123\nnewpass123\ny\n",
        )

    assert result.exit_code == 1


@pytest.mark.unit()
def test_rotate_master_rejects_same_passphrase(tmp_path: Path) -> None:
    """rotate-master exits with code 1 when old == new passphrase."""
    db_path = _make_fake_db(tmp_path)

    with (
        _NOOP_ZEROIZE,
        patch.dict(os.environ, {"OPEN_BANCA_DB_PATH": str(db_path)}, clear=False),
        patch("open_banca_cli.commands.rotate_master.ConnectionPool"),
        patch("open_banca_cli.commands.rotate_master.SecretVault"),
        patch("open_banca_cli.commands.rotate_master.migrate"),
    ):
        result = runner.invoke(
            app,
            ["rotate-master"],
            input="samepass\nsamepass\nsamepass\ny\n",
        )

    assert result.exit_code == 1
