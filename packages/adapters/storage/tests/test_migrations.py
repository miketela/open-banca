"""Tests for the migration runner and schema setup.

TDD coverage:
- Migrations create all expected tables.
- PRAGMA cipher_version confirms SQLCipher is active.
- Running migrate() twice is idempotent.
- schema_migrations table tracks applied files.
"""

from __future__ import annotations

import pytest

from open_banca_storage.connection import ConnectionPool, PassthroughKeyDerivation
from open_banca_storage.migrations import migrate


@pytest.fixture()
def in_memory_pool(tmp_path):
    """Return a ConnectionPool backed by a temp file (SQLCipher needs a file)."""
    db_file = tmp_path / "test.db"
    pool = ConnectionPool(
        db_path=db_file,
        passphrase="test-passphrase",
        key_derivation=PassthroughKeyDerivation(),
    )
    yield pool
    pool.close()


def test_cipher_version(in_memory_pool):
    """PRAGMA cipher_version must report a 4.x SQLCipher build."""
    conn = in_memory_pool.get()
    row = conn.execute("PRAGMA cipher_version;").fetchone()
    assert row is not None, "cipher_version returned no row"
    version: str = row[0]
    assert version.startswith("4."), f"Expected SQLCipher 4.x, got {version!r}"


def test_migrate_creates_all_tables(in_memory_pool):
    """migrate() must create all 8 expected tables (7 per spec + cursors)."""
    conn = in_memory_pool.get()
    migrate(conn)

    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = {r[0] for r in rows}

    expected = {
        "credentials",
        "jobs",
        "accounts",
        "transactions",
        "dedup_index",
        "audit_log",
        "webhook_outbox",
        "cursors",  # 8th table added for JobStorePort cursor methods
        "schema_migrations",
    }
    assert expected <= table_names, f"Missing tables: {expected - table_names}"


def test_migrate_idempotent(in_memory_pool):
    """Calling migrate() twice must succeed and apply no extra migrations on second run."""
    conn = in_memory_pool.get()
    first = migrate(conn)
    second = migrate(conn)

    # first run applies all pending migrations (currently 2: 001 + 002)
    assert len(first) >= 1, f"Expected at least 1 migration on first run, got {first}"
    assert second == [], f"Expected 0 migrations on second run, got {second}"


def test_schema_migrations_tracks_files(in_memory_pool):
    """schema_migrations table must record applied filenames."""
    conn = in_memory_pool.get()
    migrate(conn)

    rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
    filenames = [r[0] for r in rows]
    assert "001_initial.sql" in filenames
    assert "002_task31_extensions.sql" in filenames


def test_wrong_key_raises(tmp_path):
    """Opening an existing encrypted DB with a wrong key must raise an error."""
    db_file = tmp_path / "secure.db"

    # Create and populate with correct key
    pool_correct = ConnectionPool(
        db_path=db_file,
        passphrase="correct-passphrase",
        key_derivation=PassthroughKeyDerivation(),
    )
    conn = pool_correct.get()
    migrate(conn)
    pool_correct.close()

    # Attempt to open with wrong key
    pool_wrong = ConnectionPool(
        db_path=db_file,
        passphrase="wrong-passphrase",
        key_derivation=PassthroughKeyDerivation(),
    )
    with pytest.raises(Exception):
        pool_wrong.get()
    pool_wrong.close()


def test_audit_log_triggers_exist(in_memory_pool):
    """Both append-only triggers must be present in the schema."""
    conn = in_memory_pool.get()
    migrate(conn)

    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='audit_log'"
    ).fetchall()
    trigger_names = {r[0] for r in rows}

    assert "audit_log_no_update" in trigger_names
    assert "audit_log_no_delete" in trigger_names
