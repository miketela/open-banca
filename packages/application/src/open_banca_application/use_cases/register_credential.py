"""RegisterCredential use case — store encrypted bank credentials."""
from __future__ import annotations

import contextlib
import ctypes
from dataclasses import dataclass

from pydantic import BaseModel

from open_banca_domain.entities.credential import Credential
from open_banca_domain.ports.secret_store_port import SecretStorePort


class RegisterCredentialInput(BaseModel):
    bank: str
    plaintext: str
    label: str


@dataclass
class RegisterCredentialOutput:
    credential: Credential


class RegisterCredential:
    """Stores a credential in the vault and zeroizes the plaintext buffer."""

    def __init__(self, secret_store: SecretStorePort) -> None:
        self._secret_store = secret_store

    def execute(self, input: RegisterCredentialInput) -> RegisterCredentialOutput:
        plaintext = input.plaintext
        credential = self._secret_store.store_credential(
            bank=input.bank,
            plaintext=plaintext,
            label=input.label,
        )
        # Zeroize plaintext string in memory — best-effort on CPython.
        # Real zeroization requires bytearray; string interning limits guarantees.
        _zeroize_str(plaintext)
        return RegisterCredentialOutput(credential=credential)


def _zeroize_str(s: str) -> None:
    """Best-effort zeroization of a string's internal buffer on CPython."""
    with contextlib.suppress(Exception):
        # Write null bytes over the string's internal Py_UNICODE buffer.
        # Non-CPython runtimes raise here — silently suppressed, value is in vault.
        ctypes.memset(id(s) + 48, 0, len(s) * 2)
