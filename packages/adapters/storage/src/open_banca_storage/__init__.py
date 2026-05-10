"""open-banca SQLCipher storage adapter."""

from open_banca_storage.config import StorageSettings, get_settings
from open_banca_storage.connection import ConnectionPool, KeyDerivation, PassthroughKeyDerivation
from open_banca_storage.kdf import Argon2idKeyDerivation, derive_row_key, wipe_row_key
from open_banca_storage.migrations import migrate
from open_banca_storage.mlock_boot_check import BootSecurityError, run_boot_check
from open_banca_storage.repositories import SqliteJobStore
from open_banca_storage.secret_vault import SecretVault

__all__ = [
    "Argon2idKeyDerivation",
    "BootSecurityError",
    "ConnectionPool",
    "KeyDerivation",
    "PassthroughKeyDerivation",
    "SecretVault",
    "SqliteJobStore",
    "StorageSettings",
    "derive_row_key",
    "get_settings",
    "migrate",
    "run_boot_check",
    "wipe_row_key",
]
