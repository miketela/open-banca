"""SecretStorePort — encrypted credential vault."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from open_banca_domain.entities.credential import Credential


@runtime_checkable
class SecretStorePort(Protocol):
    """Stores and retrieves bank credentials encrypted at rest."""

    def store_credential(self, bank: str, plaintext: str, label: str) -> Credential: ...

    def fetch_credential(self, credential_ref: str) -> str: ...

    def rotate_master(self) -> None: ...
