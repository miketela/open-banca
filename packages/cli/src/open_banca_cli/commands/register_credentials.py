"""register-credentials command.

Stores bank credentials (username + password, and optionally PIN/OTP) into
the encrypted vault.  Each field is stored as a separate :class:`Credential`
row keyed by its label (e.g. ``"username"``, ``"password"``, ``"pin"``).

Usage::

    open-banca register-credentials --bank banco_general
    open-banca register-credentials --bank banco_general --label prod
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from open_banca_cli.prompts import prompt_password, zeroize_str
from open_banca_cli.vault_factory import open_vault_interactive

console = Console()

# Banks that require a PIN in addition to username/password.
_BANKS_WITH_PIN: frozenset[str] = frozenset(
    {
        "banco_general",
        "banistmo",
        "bac",
    }
)


def register_credentials(
    bank: Annotated[str, typer.Option("--bank", help="Bank identifier (e.g. banco_general)")],
    label: Annotated[
        str | None,
        typer.Option("--label", help="Human-readable label for this credential set"),
    ] = None,
) -> None:
    """Store bank credentials encrypted in the vault.

    Prompts for username, password, and (where applicable) PIN.
    Each field is stored as a separate encrypted row.
    Plaintext is zeroized from memory after storage.
    """
    effective_label = label or bank

    vault, _passphrase = open_vault_interactive()

    username: str = ""
    password: str = ""
    pin: str = ""

    try:
        typer.echo(f"\nEntering credentials for bank: {bank!r}  label: {effective_label!r}")

        username = typer.prompt("Username")
        password = prompt_password("Password")

        pin_required = bank.lower() in _BANKS_WITH_PIN
        if pin_required:
            pin = prompt_password("PIN (leave blank to skip)", allow_blank=True)

        # Confirmation summary — no plaintext displayed.
        console.print("\n[bold]Summary[/bold]")
        console.print(f"  bank  : {bank}")
        console.print(f"  label : {effective_label}")
        console.print(f"  user  : {username}")
        console.print(f"  pass  : {'*' * 8}")
        if pin_required:
            console.print(f"  pin   : {'*' * (len(pin) or 1)}")

        confirmed = typer.confirm("\nStore credentials?", default=True)
        if not confirmed:
            typer.echo("Aborted.")
            raise typer.Exit(code=0)

        # Store each non-empty field as a separate credential row.
        stored_ids: list[tuple[str, str]] = []
        fields: list[tuple[str, str]] = [
            (f"{effective_label}:username", username),
            (f"{effective_label}:password", password),
        ]
        if pin_required and pin:
            fields.append((f"{effective_label}:pin", pin))

        with vault:
            for field_label, plaintext in fields:
                cred = vault.store_credential(
                    bank=bank,
                    plaintext=plaintext,
                    label=field_label,
                )
                stored_ids.append((cred.id, field_label))

        # Zeroize all plaintext values best-effort.
        zeroize_str(username)
        zeroize_str(password)
        if pin:
            zeroize_str(pin)
        for _, plaintext in fields:
            zeroize_str(plaintext)

        # Report stored credential IDs (no plaintext).
        table = Table(title="Stored credentials", show_lines=True)
        table.add_column("credential_id", style="cyan")
        table.add_column("label", style="green")
        for cred_id, field_label in stored_ids:
            table.add_row(cred_id, field_label)

        console.print(table)
        console.print(f"[green]Stored {len(stored_ids)} credential(s) encrypted.[/green]")

    finally:
        # Always zeroize even on error paths.
        if username:
            zeroize_str(username)
        if password:
            zeroize_str(password)
        if pin:
            zeroize_str(pin)
