"""SQLCipher connection pool helper with pluggable key-derivation."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# pysqlcipher3 is the macOS binding; sqlcipher3 (from sqlcipher3-binary) is
# the Linux equivalent — both expose the same dbapi2 interface.
# Neither package ships type stubs, so we use Any for the Connection type alias
# and suppress attribute errors on the dynamic module.
try:
    from pysqlcipher3 import dbapi2 as _sqlite  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover — Linux CI uses sqlcipher3-binary
    import sqlcipher3 as _sqlite  # type: ignore[import-untyped,no-redef]

# Connection is the pysqlcipher3/sqlcipher3 DBAPI2 connection object.
# Typed as Any because neither library ships stubs.
Connection: type[Any] = getattr(_sqlite, "Connection", Any)  # type: ignore[misc]


def _sqlite_connect(db_path: str) -> Any:  # type: ignore[return]
    """Call _sqlite.connect() with type-checker suppression."""
    # check_same_thread=False: FastAPI resolves sync Depends in a threadpool
    # while async endpoints run on the event loop (same ConnectionPool, different threads).
    return _sqlite.connect(db_path, check_same_thread=False)  # type: ignore[attr-defined]


@runtime_checkable
class KeyDerivation(Protocol):
    """Contract for turning a master passphrase into a SQLCipher PRAGMA key.

    Task #5 implements the real Argon2id derivation.  Task #4 ships the
    ``PassthroughKeyDerivation`` placeholder (no KDF — passphrase IS the key).
    """

    def derive_db_key(self, passphrase: str) -> str:
        """Return a hex string or raw passphrase for PRAGMA key.

        The returned value will be used in::

            PRAGMA key = '<returned_value>';

        If you want raw bytes (hex mode), return ``"x'<hex>'"``; for
        passphrase mode return the passphrase directly.
        """
        ...


class PassthroughKeyDerivation:
    """No-op KDF — passphrase is used directly.

    Suitable ONLY for tests or dev environments with a throwaway DB.
    Task #5 replaces this with Argon2id (mem=128 MiB, iters=3).
    """

    def derive_db_key(self, passphrase: str) -> str:
        """Return passphrase unchanged."""
        return passphrase


class ConnectionPool:
    """Thread-local SQLCipher connection pool.

    Each thread gets its own ``Connection`` so we never share a connection
    across threads (SQLite connections are not thread-safe by default).

    ``PRAGMA key`` is applied as the *first* statement on every new connection,
    before any schema-level PRAGMAs or DDL — this is required by SQLCipher.

    Args:
        db_path: Path to the encrypted SQLite database file.
        passphrase: Master passphrase passed to *key_derivation*.
        key_derivation: Strategy for turning *passphrase* into the PRAGMA key.
            Defaults to ``PassthroughKeyDerivation`` (dev/test only).
    """

    def __init__(
        self,
        db_path: Path,
        passphrase: str,
        key_derivation: KeyDerivation | None = None,
    ) -> None:
        self._db_path = db_path
        self._passphrase = passphrase
        self._kdf: KeyDerivation = key_derivation or PassthroughKeyDerivation()
        self._local = threading.local()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self) -> Connection:
        """Return the thread-local connection, creating it if necessary."""
        conn: Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        """Close the thread-local connection if open."""
        conn: Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _open(self) -> Connection:
        """Open a new connection and apply PRAGMA key as the first statement."""
        conn = _sqlite_connect(str(self._db_path))
        pragma_key = self._kdf.derive_db_key(self._passphrase)
        # PRAGMA key MUST be the very first statement on a fresh connection,
        # before any schema PRAGMAs or DDL — required by SQLCipher.
        conn.execute(f"PRAGMA key = '{pragma_key}';")
        # Verify the connection is readable (raises if wrong key).
        conn.execute("SELECT count(*) FROM sqlite_master;")
        # PRAGMA foreign_keys: deliberately NOT enabled here.
        # Rationale: JobStorePort.save_transaction() writes NULL for job_id
        # (the port signature does not carry a job context), which would violate
        # a FK constraint on transactions.job_id.  Application-layer callers
        # that know the job context use save_transaction_with_job() which does
        # set a valid job_id.  If you enable FK enforcement in a future revision,
        # ensure save_transaction() is removed or all call sites use
        # save_transaction_with_job().
        return conn
