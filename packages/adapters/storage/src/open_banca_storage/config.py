"""Storage adapter configuration via environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class StorageSettings(BaseSettings):
    """Runtime configuration for the SQLCipher storage adapter.

    Environment variables:
        OPEN_BANCA_DB_PATH — absolute or relative path to the SQLCipher DB file.
            Defaults to ``./open_banca.db`` (current working directory).
        OPEN_BANCA_MASTER_PASSPHRASE — master passphrase used by the
            KeyDerivation implementation to derive the PRAGMA key.
            MUST be set in production; never commit to version control.
            An empty string is allowed here to support test environments;
            production callers must verify it is non-empty before calling
            ConnectionPool (SQLCipher 4.x rejects empty passphrases).
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    open_banca_db_path: Path = Path("./open_banca.db")
    open_banca_master_passphrase: str = ""


def get_settings() -> StorageSettings:
    """Return a fresh settings instance (reads env at call time)."""
    return StorageSettings()
