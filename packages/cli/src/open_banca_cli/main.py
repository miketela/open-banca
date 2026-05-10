"""open-banca CLI entry point.

Registers all sub-commands and provides the main Typer application.

Entry points::

    open-banca --help
    python -m open_banca_cli --help
"""

from __future__ import annotations

import typer

from open_banca_cli.commands.list_credentials import list_credentials
from open_banca_cli.commands.register_credentials import register_credentials
from open_banca_cli.commands.rotate_master import rotate_master

app = typer.Typer(
    name="open-banca",
    help="open-banca — self-hosted bank data capture CLI (credential management).",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,  # never leak locals (may contain secrets)
)

app.command("register-credentials")(register_credentials)
app.command("list-credentials")(list_credentials)
app.command("rotate-master")(rotate_master)
