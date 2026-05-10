"""Tests for SQLCipher encryption correctness.

TDD coverage:
- Correct key X opens the DB.
- Wrong key Y cannot open the DB (fails at connection or sqlite_master).
- Passphrase never stored in the DB file (binary scan).
- ConnectionPool applies PRAGMA key on every new connection.
"""

from __future__ import annotations

import pytest

from open_banca_storage.connection import ConnectionPool, PassthroughKeyDerivation
from open_banca_storage.migrations import migrate

KEY_X = "correct-key-xyz-1234"
KEY_Y = "wrong-key-abc-9999"


@pytest.fixture()
def encrypted_db(tmp_path):
    """Create an encrypted DB with KEY_X and return its path."""
    db_file = tmp_path / "encrypted.db"
    pool = ConnectionPool(
        db_path=db_file,
        passphrase=KEY_X,
        key_derivation=PassthroughKeyDerivation(),
    )
    conn = pool.get()
    migrate(conn)
    # Insert sentinel data to verify readable state
    conn.execute(
        "INSERT INTO cursors (bank, cursor, updated_at) VALUES ('test_bank', 'cursor-1', datetime('now'))"
    )
    conn.commit()
    pool.close()
    return db_file


def test_correct_key_x_opens_db(tmp_path, encrypted_db):
    """Opening with correct key X must succeed and return data."""
    pool = ConnectionPool(
        db_path=encrypted_db,
        passphrase=KEY_X,
        key_derivation=PassthroughKeyDerivation(),
    )
    conn = pool.get()
    row = conn.execute("SELECT cursor FROM cursors WHERE bank = 'test_bank'").fetchone()
    assert row is not None
    assert row[0] == "cursor-1"
    pool.close()


def test_wrong_key_y_cannot_open_db(tmp_path, encrypted_db):
    """Opening with wrong key Y must raise an exception."""
    pool = ConnectionPool(
        db_path=encrypted_db,
        passphrase=KEY_Y,
        key_derivation=PassthroughKeyDerivation(),
    )
    with pytest.raises(Exception):
        pool.get()
    pool.close()


def test_passphrase_not_in_raw_bytes(tmp_path, encrypted_db):
    """The passphrase string must NOT appear as plaintext in the DB file bytes."""
    raw_bytes = encrypted_db.read_bytes()
    assert KEY_X.encode() not in raw_bytes, (
        "Passphrase found as plaintext in encrypted DB file — encryption may not be active"
    )


def test_empty_passphrase_key(tmp_path):
    """SQLCipher 4.x rejects an empty passphrase as invalid key."""
    db_file = tmp_path / "empty_key.db"
    pool = ConnectionPool(
        db_path=db_file,
        passphrase="",
        key_derivation=PassthroughKeyDerivation(),
    )
    # SQLCipher 4.x requires a non-empty key; empty passphrase must raise.
    with pytest.raises(Exception):
        pool.get()
    pool.close()


def test_custom_key_derivation(tmp_path):
    """A custom KeyDerivation is called to produce the PRAGMA key."""

    class PrefixKDF:
        """Prepends 'derived:' to the passphrase for testing."""

        called_with: str | None = None

        def derive_db_key(self, passphrase: str) -> str:
            PrefixKDF.called_with = passphrase
            return f"derived:{passphrase}"

    kdf = PrefixKDF()
    db_file = tmp_path / "custom_kdf.db"
    pool = ConnectionPool(db_path=db_file, passphrase="mypass", key_derivation=kdf)
    conn = pool.get()
    migrate(conn)
    pool.close()

    assert PrefixKDF.called_with == "mypass"

    # Re-open with same KDF to confirm derived key works
    pool2 = ConnectionPool(db_path=db_file, passphrase="mypass", key_derivation=PrefixKDF())
    conn2 = pool2.get()
    rows = conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert len(rows) > 0
    pool2.close()
