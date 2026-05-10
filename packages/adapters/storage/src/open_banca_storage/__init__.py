"""open-banca SQLCipher storage adapter."""

from open_banca_storage.config import StorageSettings, get_settings
from open_banca_storage.connection import ConnectionPool, KeyDerivation, PassthroughKeyDerivation
from open_banca_storage.dedup import DedupEngine, IngestResult, TransferMatcher, compute_fingerprint
from open_banca_storage.migrations import migrate
from open_banca_storage.repositories import SqliteJobStore

__all__ = [
    "ConnectionPool",
    "DedupEngine",
    "IngestResult",
    "KeyDerivation",
    "PassthroughKeyDerivation",
    "SqliteJobStore",
    "StorageSettings",
    "TransferMatcher",
    "compute_fingerprint",
    "get_settings",
    "migrate",
]
