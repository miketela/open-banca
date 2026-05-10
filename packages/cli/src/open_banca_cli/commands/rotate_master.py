"""rotate-master command.

Re-encrypts all credentials in the vault with a new master passphrase.

The implementation:
1. Prompts for the OLD master passphrase (to open the vault).
2. Prompts for the NEW master passphrase (with confirmation).
3. Sets ``OPEN_BANCA_MASTER_PASSPHRASE_NEW`` in the environment.
4. Calls ``SecretVault.rotate_master()`` which re-encrypts all rows and
   issues a SQLCipher ``PRAGMA rekey``.
5. Clears the env var after rotation.

Usage::

    open-banca rotate-master
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console

from open_banca_cli.prompts import prompt_password, prompt_password_confirmed, zeroize_str
from open_banca_storage import Argon2idKeyDerivation, ConnectionPool, SecretVault, migrate

console = Console()


def rotate_master() -> None:
    """Re-encrypt all vault credentials with a new master passphrase.

    Prompts for the old and new master passphrases interactively.
    The vault DB is re-keyed atomically; partial failure is rolled back.
    """
    db_path_raw = os.environ.get("OPEN_BANCA_DB_PATH", "./open_banca.db")
    db_path = Path(db_path_raw)

    if not db_path.exists():
        typer.echo(
            f"Error: vault not found at {db_path.resolve()}.  "
            "Run 'register-credentials' first to initialise.",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo("Rotating master passphrase.")
    typer.echo("[WARNING] The API must be stopped before rotation.")

    old_passphrase = prompt_password("Current master passphrase")
    if not old_passphrase:
        typer.echo("Error: passphrase cannot be empty.", err=True)
        raise typer.Exit(code=1)

    new_passphrase = prompt_password_confirmed("New master passphrase")
    if not new_passphrase:
        typer.echo("Error: new passphrase cannot be empty.", err=True)
        raise typer.Exit(code=1)

    if old_passphrase == new_passphrase:
        typer.echo("Error: new passphrase must differ from current passphrase.", err=True)
        raise typer.Exit(code=1)

    confirmed = typer.confirm("\nThis will re-encrypt all credentials. Continue?", default=False)
    if not confirmed:
        typer.echo("Aborted.")
        raise typer.Exit(code=0)

    # Set new passphrase in env for SecretVault.rotate_master() to read.
    os.environ["OPEN_BANCA_MASTER_PASSPHRASE_NEW"] = new_passphrase

    try:
        pool = ConnectionPool(
            db_path=db_path,
            passphrase=old_passphrase,
            key_derivation=Argon2idKeyDerivation(),
        )
        migrate(pool.get())

        with SecretVault(pool=pool, master_passphrase=old_passphrase) as vault:
            vault.rotate_master()

        console.print("[green]Master passphrase rotated successfully.[/green]")
        console.print(
            "[dim]Update OPEN_BANCA_MASTER_PASSPHRASE in your environment to the new value.[/dim]"
        )
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        typer.echo(f"Rotation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        # Always clear the env var and zeroize passphrases.
        os.environ.pop("OPEN_BANCA_MASTER_PASSPHRASE_NEW", None)
        zeroize_str(old_passphrase)
        zeroize_str(new_passphrase)
