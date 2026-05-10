"""Factory helpers for constructing a :class:`SecretVault` in CLI commands.

The CLI needs to open the vault with a master passphrase obtained either from
the ``OPEN_BANCA_MASTER_PASSPHRASE`` environment variable or from an
interactive prompt.  This module centralises that wiring so each command does
not repeat the boilerplate.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer

from open_banca_cli.prompts import prompt_password, prompt_password_confirmed
from open_banca_storage import (
    Argon2idKeyDerivation,
    ConnectionPool,
    SecretVault,
    migrate,
)


def _db_path() -> Path:
    """Return the DB path from env (``OPEN_BANCA_DB_PATH``) or a default."""
    raw = os.environ.get("OPEN_BANCA_DB_PATH", "./open_banca.db")
    return Path(raw)


def _is_vault_initialized(db_path: Path) -> bool:
    """Return True if the vault DB file exists and is non-empty."""
    return db_path.exists() and db_path.stat().st_size > 0


def open_vault_interactive() -> tuple[SecretVault, str]:
    """Open (or initialize) the vault, prompting for the master passphrase.

    Behaviour:
    - If ``OPEN_BANCA_MASTER_PASSPHRASE`` is set in the environment, it is
      used directly (non-interactive; suitable for scripting).
    - If the DB does not exist yet (first run), prompt to CREATE and CONFIRM
      a new master passphrase.
    - If the DB already exists, prompt for the existing master passphrase.

    Returns:
        A tuple of ``(SecretVault, master_passphrase_str)`` where the vault
        is fully initialized (migrations applied).  The caller is responsible
        for closing the vault via the context manager or ``vault.close()``.
    """
    db_path = _db_path()
    env_passphrase = os.environ.get("OPEN_BANCA_MASTER_PASSPHRASE", "")

    if env_passphrase:
        passphrase = env_passphrase
    elif not _is_vault_initialized(db_path):
        typer.echo("No vault found. Creating a new encrypted vault at: " + str(db_path.resolve()))
        typer.echo("Choose a strong master passphrase — it cannot be recovered if lost.")
        passphrase = prompt_password_confirmed("New master passphrase")
    else:
        passphrase = prompt_password("Master passphrase")

    if not passphrase:
        typer.echo("Error: master passphrase cannot be empty.", err=True)
        raise typer.Exit(code=1)

    pool = ConnectionPool(
        db_path=db_path,
        passphrase=passphrase,
        key_derivation=Argon2idKeyDerivation(),
    )
    # Apply pending migrations (idempotent).
    migrate(pool.get())

    vault = SecretVault(pool=pool, master_passphrase=passphrase)
    return vault, passphrase
