"""Secure prompt helpers for the open-banca CLI.

All password/passphrase inputs use ``typer.prompt(hide_input=True)`` which
calls Click's prompt machinery backed by ``getpass`` — no terminal echo.

Plaintext values returned from these helpers are Python ``str`` objects.
Callers are responsible for zeroizing them after use via ``_zeroize_str``.
"""

from __future__ import annotations

import contextlib
import ctypes

import typer


def prompt_password(prompt_text: str) -> str:
    """Prompt for a secret value with no echo.

    Uses ``typer.prompt(hide_input=True)`` which delegates to Click /
    ``getpass`` internally.  The returned string is NOT confirmed.
    """
    return typer.prompt(prompt_text, hide_input=True)


def prompt_password_confirmed(prompt_text: str) -> str:
    """Prompt for a secret value with confirmation (no echo on either entry).

    Raises :class:`typer.Abort` if the two entries do not match.
    """
    value: str = typer.prompt(
        prompt_text,
        hide_input=True,
        confirmation_prompt=True,
    )
    return value


def zeroize_str(s: str) -> None:
    """Best-effort zeroization of a string's internal buffer on CPython.

    Non-CPython runtimes silently suppress errors — the value is already
    in the encrypted vault by the time this is called.
    """
    with contextlib.suppress(Exception):
        ctypes.memset(id(s) + 48, 0, len(s) * 2)
