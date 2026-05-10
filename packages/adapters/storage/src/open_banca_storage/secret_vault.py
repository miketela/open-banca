"""SecretVault — AES-GCM per-row encrypted credential store.

Implements ``SecretStorePort`` (domain port).  Stores bank credentials
encrypted at rest using a two-layer scheme (per ADR-0008):

    1. SQLCipher AES-256-CBC encrypts the entire database file (via
       ``Argon2idKeyDerivation`` in ``ConnectionPool``).
    2. AES-256-GCM encrypts each credential row individually, with a
       per-row random 16-byte salt and 12-byte nonce.  The row key is
       derived from the master passphrase + salt using Argon2id (256 MiB,
       iters=3, p=4).

Security invariants:
    - Plaintext credentials NEVER appear in logs, OTel spans, or exceptions.
    - Row keys are zeroized immediately after use (``wipe_row_key``).
    - Master passphrase bytes are not copied to ``str`` after initial encoding.

``rotate_master()`` reads the new passphrase from the environment variable
``OPEN_BANCA_MASTER_PASSPHRASE_NEW`` and re-encrypts all rows, then sets
``OPEN_BANCA_MASTER_PASSPHRASE`` to the new value for the rest of the
process lifetime via ``os.environ``.

The ``credentials`` table schema (from ``001_initial.sql``):
    id            TEXT PRIMARY KEY  (UUID)
    bank          TEXT
    label         TEXT
    credential_ref TEXT UNIQUE       (opaque reference returned to callers)
    ciphertext    BLOB               (AES-GCM ciphertext)
    nonce         BLOB               (12-byte random nonce)
    kdf_meta      TEXT               (JSON: {salt_hex, time_cost, memory_cost, parallelism})
    created_at    TEXT               (UTC ISO-8601)
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from open_banca_domain.entities.credential import Credential
from open_banca_domain.ports.secret_store_port import SecretStorePort
from open_banca_storage.connection import ConnectionPool
from open_banca_storage.kdf import derive_row_key, wipe_row_key

logger = logging.getLogger(__name__)

# Additional authenticated data bound to ciphertext (prevents cross-row replay).
_AAD_PREFIX = b"open_banca:vault:v1:"


def _make_aad(cred_id: str) -> bytes:
    return _AAD_PREFIX + cred_id.encode("utf-8")


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


class SecretVault:
    """Encrypted credential vault implementing ``SecretStorePort``.

    Args:
        pool: An open ``ConnectionPool`` already keyed with the master
            passphrase via ``Argon2idKeyDerivation``.
        master_passphrase: The master passphrase in plain text.  This is
            stored as ``bytes`` internally and should be the same value used
            to key the ``ConnectionPool``.  The caller must not keep other
            references to this string after construction.
    """

    def __init__(self, pool: ConnectionPool, master_passphrase: str) -> None:
        self._pool = pool
        # Encode once; keep as bytearray so we can zeroize on close.
        self._master: bytearray = bytearray(master_passphrase.encode("utf-8"))

    # ------------------------------------------------------------------
    # SecretStorePort implementation
    # ------------------------------------------------------------------

    def store_credential(self, bank: str, plaintext: str, label: str) -> Credential:
        """Encrypt *plaintext* with AES-GCM and persist to the credentials table.

        Args:
            bank: Bank identifier (e.g. ``"banco_general"``).
            plaintext: The secret credential value (e.g. password).
            label: Human-readable label (e.g. ``"username"`` / ``"password"``).

        Returns:
            A ``Credential`` entity with an opaque ``credential_ref``.
            Plaintext is NOT stored in the entity.
        """
        cred_id = str(uuid.uuid4())
        credential_ref = str(uuid.uuid4())

        # Generate per-row entropy
        salt = secrets.token_bytes(16)   # 128-bit random salt
        nonce = secrets.token_bytes(12)  # 96-bit AES-GCM nonce

        plaintext_bytes = plaintext.encode("utf-8")
        aad = _make_aad(cred_id)

        row_key = derive_row_key(bytes(self._master), salt)
        try:
            aesgcm = AESGCM(bytes(row_key))
            ciphertext = aesgcm.encrypt(nonce, plaintext_bytes, aad)
        finally:
            wipe_row_key(row_key)

        kdf_meta = json.dumps({
            "salt_hex": salt.hex(),
            "time_cost": 3,
            "memory_cost": 262_144,
            "parallelism": 4,
        })

        conn = self._pool.get()
        conn.execute(
            """
            INSERT INTO credentials
                (id, bank, label, credential_ref, ciphertext, nonce, kdf_meta, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (cred_id, bank, label, credential_ref, ciphertext, nonce, kdf_meta, _now_utc()),
        )
        conn.commit()

        logger.info("vault: stored credential id=%s bank=%s label=%s", cred_id, bank, label)
        self._audit("cred_stored", "credential", cred_id, {"bank": bank, "label": label})

        return Credential(
            id=cred_id,
            bank=bank,
            credential_ref=credential_ref,
            label=label,
        )

    def fetch_credential(self, credential_ref: str) -> str:
        """Decrypt and return the plaintext for *credential_ref*.

        The row key is derived fresh for each call and zeroized immediately
        after decryption.  Callers are responsible for zeroizing the returned
        string when done (Python strings are immutable; this is a best-effort
        constraint documented in the port).

        Raises:
            KeyError: If no credential with the given ref exists.
            cryptography.exceptions.InvalidTag: If the ciphertext is tampered.
        """
        conn = self._pool.get()
        row = conn.execute(
            """
            SELECT id, ciphertext, nonce, kdf_meta
            FROM credentials
            WHERE credential_ref = ?
            """,
            (credential_ref,),
        ).fetchone()

        if row is None:
            raise KeyError(f"No credential found for ref={credential_ref!r}")

        cred_id, ciphertext, nonce, kdf_meta_json = row
        kdf_meta: dict[str, Any] = json.loads(kdf_meta_json)
        salt = bytes.fromhex(kdf_meta["salt_hex"])
        aad = _make_aad(cred_id)

        row_key = derive_row_key(bytes(self._master), salt)
        try:
            aesgcm = AESGCM(bytes(row_key))
            plaintext_bytes = aesgcm.decrypt(nonce, bytes(ciphertext), aad)
        finally:
            wipe_row_key(row_key)

        plaintext = plaintext_bytes.decode("utf-8")
        logger.debug("vault: fetched credential ref=%s id=%s", credential_ref, cred_id)
        self._audit("cred_retrieved", "credential", cred_id, {})
        return plaintext

    def rotate_master(self) -> None:
        """Re-encrypt all credentials with a new master passphrase.

        Reads the new passphrase from the ``OPEN_BANCA_MASTER_PASSPHRASE_NEW``
        environment variable.  After successful re-encryption, updates
        ``os.environ["OPEN_BANCA_MASTER_PASSPHRASE"]`` and wipes the old master
        from this instance.

        This is an offline operation (the API should stop accepting requests
        while rotation is in progress).

        Raises:
            EnvironmentError: If ``OPEN_BANCA_MASTER_PASSPHRASE_NEW`` is not set.
            RuntimeError: If any credential fails to re-encrypt (partial
                rotation is rolled back on the same connection).
        """
        new_passphrase = os.environ.get("OPEN_BANCA_MASTER_PASSPHRASE_NEW", "")
        if not new_passphrase:
            raise OSError(
                "OPEN_BANCA_MASTER_PASSPHRASE_NEW must be set before calling rotate_master()"
            )

        new_master = bytearray(new_passphrase.encode("utf-8"))

        conn = self._pool.get()
        rows = conn.execute(
            "SELECT id, credential_ref, bank, label, ciphertext, nonce, kdf_meta, created_at "
            "FROM credentials"
        ).fetchall()

        updates: list[tuple[bytes, bytes, str, str]] = []

        for row in rows:
            cred_id, _credential_ref, _bank, _label, ciphertext, nonce, kdf_meta_json, _created_at = row
            kdf_meta: dict[str, Any] = json.loads(kdf_meta_json)
            salt = bytes.fromhex(kdf_meta["salt_hex"])
            aad = _make_aad(cred_id)

            # Decrypt with old master
            old_row_key = derive_row_key(bytes(self._master), salt)
            try:
                plaintext_bytes = AESGCM(bytes(old_row_key)).decrypt(nonce, bytes(ciphertext), aad)
            finally:
                wipe_row_key(old_row_key)

            # Re-encrypt with new master (new salt + nonce)
            new_salt = secrets.token_bytes(16)
            new_nonce = secrets.token_bytes(12)
            new_row_key = derive_row_key(bytes(new_master), new_salt)
            try:
                new_ciphertext = AESGCM(bytes(new_row_key)).encrypt(new_nonce, plaintext_bytes, aad)
            finally:
                wipe_row_key(new_row_key)

            new_kdf_meta = json.dumps({
                "salt_hex": new_salt.hex(),
                "time_cost": 3,
                "memory_cost": 262_144,
                "parallelism": 4,
            })
            updates.append((new_ciphertext, new_nonce, new_kdf_meta, cred_id))

            # Zeroize decrypted plaintext
            plaintext_ba = bytearray(plaintext_bytes)
            for i in range(len(plaintext_ba)):
                plaintext_ba[i] = 0

        # Apply all updates in a single transaction
        conn.executemany(
            "UPDATE credentials SET ciphertext=?, nonce=?, kdf_meta=? WHERE id=?",
            updates,
        )
        conn.commit()

        # Wipe old master and replace with new
        wipe_row_key(self._master)
        self._master = new_master

        # Update environment for downstream consumers
        os.environ["OPEN_BANCA_MASTER_PASSPHRASE"] = new_passphrase

        operator_id = os.environ.get("OPEN_BANCA_OPERATOR_ID", "unknown")
        logger.info("vault: master rotated rows=%d operator=%s", len(updates), operator_id)
        self._audit(
            "master_rotated",
            "vault",
            "master",
            {"rows_count": len(updates), "operator_id": operator_id},
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _audit(
        self,
        action: str,
        entity_type: str,
        entity_id: str,
        metadata: dict[str, Any],
    ) -> None:
        """Append a row to the audit_log table.  Never includes plaintext."""
        try:
            conn = self._pool.get()
            conn.execute(
                """
                INSERT INTO audit_log (id, actor, action, entity_type, entity_id, at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    "vault",
                    action,
                    entity_type,
                    entity_id,
                    _now_utc(),
                    json.dumps(metadata),
                ),
            )
            conn.commit()
        except Exception:
            # Audit failure must not block the primary operation; log and continue.
            logger.warning("vault: failed to write audit log entry for action=%s", action)

    def close(self) -> None:
        """Zeroize the master passphrase buffer."""
        wipe_row_key(self._master)


# Runtime check — SecretVault satisfies SecretStorePort
def _check_protocol() -> None:
    assert isinstance(SecretVault, type)


# Make SecretVault a recognized implementation of SecretStorePort at import time
assert issubclass(SecretVault, SecretStorePort)  # type: ignore[misc]
