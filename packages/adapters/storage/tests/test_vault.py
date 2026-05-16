"""Tests for SecretVault — Argon2id KDF + AES-GCM per-row encryption.

TDD coverage (per task-5 acceptance criteria):

1. KAT (known-answer-test) for Argon2id DB-level derivation.
2. AES-GCM roundtrip: encrypt then decrypt returns plaintext.
3. AES-GCM tamper-detect: flipping a ciphertext byte raises InvalidTag.
4. Zeroize verification: row key is all-zeros after wipe_row_key().
5. rotate_master flow: old and new keys both work before / after rotation.
6. store_credential / fetch_credential via SecretVault.
7. store_credential does NOT appear as plaintext in the DB file.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from open_banca_storage.connection import ConnectionPool
from open_banca_storage.kdf import (
    _DB_HASH_LEN,
    _DB_MEMORY_COST,
    _DB_PARALLELISM,
    _DB_SALT,
    _DB_TIME_COST,
    _ROW_HASH_LEN,
    _ROW_MEMORY_COST,
    _ROW_PARALLELISM,
    _ROW_TIME_COST,
    Argon2idKeyDerivation,
    derive_row_key,
    wipe_row_key,
)
from open_banca_storage.migrations import migrate
from open_banca_storage.secret_vault import SecretVault

# ── Helpers ──────────────────────────────────────────────────────────────────

PASSPHRASE = "test-master-passphrase-1234"


@pytest.fixture()
def vault_db(tmp_path: Path) -> tuple[SecretVault, ConnectionPool]:
    """Provision a full SecretVault backed by a temp SQLCipher DB."""
    db_file = tmp_path / "vault.db"
    kdf = Argon2idKeyDerivation()
    pool = ConnectionPool(db_path=db_file, passphrase=PASSPHRASE, key_derivation=kdf)
    migrate(pool.get())
    vault = SecretVault(pool=pool, master_passphrase=PASSPHRASE)
    return vault, pool


# ── 1. KAT — Argon2id DB-level derivation ────────────────────────────────────


def test_argon2id_db_kat() -> None:
    """Known-answer-test: Argon2idKeyDerivation is deterministic.

    We compute the expected output independently and verify the KDF matches.
    """
    passphrase = "kat-passphrase"
    secret = passphrase.encode("utf-8")

    expected_raw: bytes = hash_secret_raw(
        secret=secret,
        salt=_DB_SALT,
        time_cost=_DB_TIME_COST,
        memory_cost=_DB_MEMORY_COST,
        parallelism=_DB_PARALLELISM,
        hash_len=_DB_HASH_LEN,
        type=Type.ID,
    )
    expected_hex = expected_raw.hex()

    kdf = Argon2idKeyDerivation()
    result = kdf.derive_db_key(passphrase)

    assert result == expected_hex
    assert len(result) == 64  # 32 bytes = 64 hex chars
    assert all(c in "0123456789abcdef" for c in result)


def test_argon2id_db_params_meet_owasp() -> None:
    """Verify Argon2id DB-level params exceed OWASP 2024 minimums."""
    assert _DB_TIME_COST >= 3, "time_cost must be >= 3 (OWASP 2024)"
    assert _DB_MEMORY_COST >= 65_536, "memory_cost must be >= 64 MiB (OWASP 2024)"


def test_argon2id_row_params_meet_adr() -> None:
    """Verify per-row Argon2id params match ADR-0008 (mem=256 MiB, iters=3)."""
    assert _ROW_TIME_COST == 3
    assert _ROW_MEMORY_COST == 262_144  # 256 MiB
    assert _ROW_PARALLELISM == 4
    assert _ROW_HASH_LEN == 32


# ── 2. AES-GCM roundtrip ─────────────────────────────────────────────────────


def test_aesgcm_roundtrip() -> None:
    """Encrypt then decrypt a known plaintext; must return the original."""
    import secrets as sec

    salt = sec.token_bytes(16)
    passphrase_bytes = b"roundtrip-passphrase"
    nonce = sec.token_bytes(12)
    plaintext = b"super-secret-password-1234"
    aad = b"open_banca:vault:v1:some-cred-id"

    row_key = derive_row_key(passphrase_bytes, salt)
    try:
        ciphertext = AESGCM(bytes(row_key)).encrypt(nonce, plaintext, aad)
    finally:
        wipe_row_key(row_key)

    # Decrypt
    row_key2 = derive_row_key(passphrase_bytes, salt)
    try:
        recovered = AESGCM(bytes(row_key2)).decrypt(nonce, ciphertext, aad)
    finally:
        wipe_row_key(row_key2)

    assert recovered == plaintext


def test_aesgcm_tamper_detect() -> None:
    """Flipping a byte in the ciphertext must raise InvalidTag."""
    import secrets as sec

    salt = sec.token_bytes(16)
    passphrase_bytes = b"tamper-test-passphrase"
    nonce = sec.token_bytes(12)
    plaintext = b"sensitive-data-do-not-tamper"
    aad = b"open_banca:vault:v1:tamper-cred"

    row_key = derive_row_key(passphrase_bytes, salt)
    try:
        ciphertext = AESGCM(bytes(row_key)).encrypt(nonce, plaintext, aad)
    finally:
        wipe_row_key(row_key)

    # Flip the first byte of ciphertext
    tampered = bytes([ciphertext[0] ^ 0xFF]) + ciphertext[1:]

    row_key3 = derive_row_key(passphrase_bytes, salt)
    try:
        with pytest.raises(InvalidTag):
            AESGCM(bytes(row_key3)).decrypt(nonce, tampered, aad)
    finally:
        wipe_row_key(row_key3)


# ── 3. Zeroize verification ───────────────────────────────────────────────────


def test_wipe_row_key_zeroizes() -> None:
    """After wipe_row_key, the buffer must be all zeros."""
    import secrets as sec

    salt = sec.token_bytes(16)
    row_key = derive_row_key(b"wipe-test-pass", salt)

    # Ensure non-zero before wipe
    assert any(b != 0 for b in row_key), "derive_row_key returned all-zeros buffer (unexpected)"

    wipe_row_key(row_key)

    assert all(b == 0 for b in row_key), "wipe_row_key did not zero the buffer"


# ── 4. SecretVault store + fetch roundtrip ────────────────────────────────────


@pytest.mark.unit()
def test_vault_store_and_fetch(vault_db: tuple[SecretVault, ConnectionPool]) -> None:
    """store_credential + fetch_credential must roundtrip the plaintext."""
    vault, _pool = vault_db

    cred = vault.store_credential(
        bank="banco_general",
        plaintext="my-secret-password",
        label="password",
    )

    assert cred.bank == "banco_general"
    assert cred.label == "password"
    assert cred.credential_ref  # non-empty opaque ref

    recovered = vault.fetch_credential(cred.credential_ref)
    assert recovered == "my-secret-password"


@pytest.mark.unit()
def test_vault_plaintext_not_in_db_file(
    vault_db: tuple[SecretVault, ConnectionPool],
    tmp_path: Path,
) -> None:
    """The plaintext credential must NOT appear as bytes in the DB file."""
    vault, pool = vault_db
    plaintext = "SUPER_SECRET_CANARY_VALUE_xzqk9"

    vault.store_credential(bank="banco_general", plaintext=plaintext, label="password")
    pool.close()

    # Find the DB file by looking at the pool's path attribute
    db_path = pool._db_path
    raw = db_path.read_bytes()
    assert plaintext.encode("utf-8") not in raw, "Plaintext credential found unencrypted in DB file"


@pytest.mark.unit()
def test_vault_fetch_unknown_ref(vault_db: tuple[SecretVault, ConnectionPool]) -> None:
    """Fetching a non-existent credential_ref raises KeyError."""
    vault, _pool = vault_db
    with pytest.raises(KeyError):
        vault.fetch_credential("00000000-0000-0000-0000-000000000000")


# ── 5. rotate_master flow ─────────────────────────────────────────────────────


@pytest.mark.unit()
def test_rotate_master_re_encrypts(tmp_path: Path) -> None:
    """After rotate_master, fetch_credential still returns the correct plaintext."""
    old_passphrase = "old-master-pass-ABCD"
    new_passphrase = "new-master-pass-WXYZ"

    db_file = tmp_path / "rotate.db"
    kdf = Argon2idKeyDerivation()
    pool = ConnectionPool(db_path=db_file, passphrase=old_passphrase, key_derivation=kdf)
    migrate(pool.get())
    vault = SecretVault(pool=pool, master_passphrase=old_passphrase)

    cred1 = vault.store_credential("banco_general", "password-1", "password")
    cred2 = vault.store_credential("bac", "password-2", "token")

    # Rotate
    with patch.dict(os.environ, {"OPEN_BANCA_MASTER_PASSPHRASE_NEW": new_passphrase}):
        vault.rotate_master()

    # Both credentials should still be fetchable with the new master
    assert vault.fetch_credential(cred1.credential_ref) == "password-1"
    assert vault.fetch_credential(cred2.credential_ref) == "password-2"


@pytest.mark.unit()
def test_rotate_master_requires_env_var(vault_db: tuple[SecretVault, ConnectionPool]) -> None:
    """rotate_master() must raise OSError if OPEN_BANCA_MASTER_PASSPHRASE_NEW is unset."""
    vault, _pool = vault_db
    env_without_new = {
        k: v for k, v in os.environ.items() if k != "OPEN_BANCA_MASTER_PASSPHRASE_NEW"
    }
    with patch.dict(os.environ, env_without_new, clear=True):
        with pytest.raises(OSError):
            vault.rotate_master()


@pytest.mark.unit()
def test_rotate_master_rekeyes_sqlcipher_db(tmp_path: Path) -> None:
    """rotate_master re-keys the SQLCipher DB file via PRAGMA rekey.

    After rotation and a fresh connection pool opened with the new passphrase,
    fetch_credential must still return the correct plaintext.  This verifies
    that PRAGMA rekey was called with the new Argon2id-derived key.
    """
    old_passphrase = "rekey-old-master-1234"
    new_passphrase = "rekey-new-master-5678"

    db_file = tmp_path / "rekey.db"
    kdf = Argon2idKeyDerivation()

    # --- Phase 1: create vault with old master, store a credential, rotate ---
    pool_old = ConnectionPool(db_path=db_file, passphrase=old_passphrase, key_derivation=kdf)
    migrate(pool_old.get())
    vault = SecretVault(pool=pool_old, master_passphrase=old_passphrase)
    cred = vault.store_credential("banco_general", "rekey-secret", "password")

    with patch.dict(os.environ, {"OPEN_BANCA_MASTER_PASSPHRASE_NEW": new_passphrase}):
        vault.rotate_master()

    # Close the old pool (connection was opened with old key, rekey'd in-place)
    pool_old.close()

    # --- Phase 2: open a FRESH pool with the NEW passphrase only ---
    pool_new = ConnectionPool(db_path=db_file, passphrase=new_passphrase, key_derivation=kdf)
    vault2 = SecretVault(pool=pool_new, master_passphrase=new_passphrase)
    result = vault2.fetch_credential(cred.credential_ref)
    pool_new.close()

    assert result == "rekey-secret", (
        "fetch_credential returned wrong value after master rotation + DB rekey"
    )
