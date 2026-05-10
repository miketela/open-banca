"""list-credentials command.

Displays stored credential metadata (no plaintext) in a rich table.

Usage::

    open-banca list-credentials
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from open_banca_cli.vault_factory import open_vault_interactive

console = Console()


def list_credentials() -> None:
    """List all stored credential metadata.

    Displays credential_id, bank, label, and created_at.
    No plaintext values are shown.
    """
    vault, _passphrase = open_vault_interactive()

    with vault:
        summaries = vault.list_credentials()

    if not summaries:
        typer.echo("No credentials stored yet.")
        return

    table = Table(title="Stored Credentials", show_lines=True)
    table.add_column("credential_id", style="cyan", no_wrap=True)
    table.add_column("bank", style="yellow")
    table.add_column("label", style="green")
    table.add_column("created_at", style="dim")

    for summary in summaries:
        table.add_row(summary.id, summary.bank, summary.label, summary.created_at)

    console.print(table)
    console.print(f"[dim]{len(summaries)} credential(s) total.[/dim]")
