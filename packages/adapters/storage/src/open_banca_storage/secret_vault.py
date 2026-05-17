"""SecretVault — AES-GCM per-row encrypted credential store.

Implements ``SecretStorePort`` (domain port).  Stores bank credentials
encrypted at rest using a two-layer scheme (per ADR-0008).

Also exposes a ``security_q`` namespace (ADR-0021) for caching answers to
bank security questions used by the ``prompt_user`` step type.  The crypto
scheme is identical to credentials: per-row Argon2id KDF + AES-GCM, with the
answer plaintext never appearing in logs, OTel spans, or audit entries.

``security_answers`` table schema (from ``003_security_answers.sql``):
    credential_id   TEXT  (FK to credentials.id)
    question_hash   TEXT  (sha256 hex of the cache_key)
    field_key       TEXT  (stable human-readable key, e.g. security_q_mother_color)
    ciphertext      BLOB  (AES-GCM ciphertext)
    nonce           BLOB  (12-byte random nonce)
    kdf_meta        TEXT  (JSON: {salt_hex, time_cost, memory_cost, parallelism})
    created_at      TEXT  (UTC ISO-8601)
    expires_at      TEXT  (UTC ISO-8601)
    last_used_at    TEXT  (UTC ISO-8601, NULL until first hit)

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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from open_banca_domain.entities.credential import Credential
from open_banca_domain.ports.secret_store_port import SecretStorePort
from open_banca_storage.connection import ConnectionPool
from open_banca_storage.kdf import Argon2idKeyDerivation, derive_row_key, wipe_row_key

logger = logging.getLogger(__name__)


_DEFAULT_SECURITY_ANSWER_TTL_DAYS: int = 90


@dataclass(frozen=True)
class QuestionMeta:
    """Metadata for a cached security answer — no plaintext.

    Returned by :meth:`SecretVault.list_security_questions`.
    """

    credential_id: str
    question_hash: str
    field_key: str
    created_at: str
    expires_at: str
    last_used_at: str | None


@dataclass(frozen=True)
class CredentialSummary:
    """Read-only summary of a stored credential — no plaintext.

    Returned by :meth:`SecretVault.list_credentials` for display purposes.
    """

    id: str
    bank: str
    label: str
    created_at: str


# Additional authenticated data bound to ciphertext (prevents cross-row replay).
_AAD_PREFIX = b"open_banca:vault:v1:"
_AAD_SECURITY_Q_PREFIX = b"open_banca:security_q:v1:"


def _make_aad(cred_id: str) -> bytes:
    return _AAD_PREFIX + cred_id.encode("utf-8")


def _make_security_q_aad(credential_id: str, question_hash: str) -> bytes:
    return (
        _AAD_SECURITY_Q_PREFIX
        + credential_id.encode("utf-8")
        + b":"
        + question_hash.encode("utf-8")
    )


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
        salt = secrets.token_bytes(16)  # 128-bit random salt
        nonce = secrets.token_bytes(12)  # 96-bit AES-GCM nonce

        plaintext_bytes = plaintext.encode("utf-8")
        aad = _make_aad(cred_id)

        row_key = derive_row_key(bytes(self._master), salt)
        try:
            aesgcm = AESGCM(bytes(row_key))
            ciphertext = aesgcm.encrypt(nonce, plaintext_bytes, aad)
        finally:
            wipe_row_key(row_key)

        kdf_meta = json.dumps(
            {
                "salt_hex": salt.hex(),
                "time_cost": 3,
                "memory_cost": 262_144,
                "parallelism": 4,
            }
        )

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

        Accepts either the opaque UUID ``credential_ref`` (canonical) or the
        human-readable ``label`` (e.g. ``"personal:username"``) for convenience
        — falls back to label lookup when the ref string does not match a UUID
        row directly. Label collisions return the most recent credential.

        The row key is derived fresh for each call and zeroized immediately
        after decryption.  Callers are responsible for zeroizing the returned
        string when done (Python strings are immutable; this is a best-effort
        constraint documented in the port).

        Raises:
            KeyError: If no credential with the given ref/label exists.
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
            row = conn.execute(
                """
                SELECT id, ciphertext, nonce, kdf_meta
                FROM credentials
                WHERE label = ?
                ORDER BY created_at DESC
                LIMIT 1
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
            (
                cred_id,
                _credential_ref,
                _bank,
                _label,
                ciphertext,
                nonce,
                kdf_meta_json,
                _created_at,
            ) = row
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

            new_kdf_meta = json.dumps(
                {
                    "salt_hex": new_salt.hex(),
                    "time_cost": 3,
                    "memory_cost": 262_144,
                    "parallelism": 4,
                }
            )
            updates.append((new_ciphertext, new_nonce, new_kdf_meta, cred_id))

            # Zeroize decrypted plaintext
            plaintext_ba = bytearray(plaintext_bytes)
            for i in range(len(plaintext_ba)):
                plaintext_ba[i] = 0

        # Apply all row updates in a single transaction
        conn.executemany(
            "UPDATE credentials SET ciphertext=?, nonce=?, kdf_meta=? WHERE id=?",
            updates,
        )
        conn.commit()

        # Rekey the SQLCipher DB file with the new Argon2id-derived passphrase.
        # PRAGMA rekey must be issued BEFORE swapping the in-memory master so
        # that the current connection (still opened with the old key) can
        # authenticate the rekey.  After rekey, all NEW connections must use
        # the new derive_db_key() result.
        new_db_key = Argon2idKeyDerivation().derive_db_key(new_passphrase)
        conn.execute(f"PRAGMA rekey = '{new_db_key}';")

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
    # security_q namespace — ADR-0021
    # ------------------------------------------------------------------

    def store_security_answer(
        self,
        credential_id: str,
        question_hash: str,
        answer: str,
        *,
        field_key: str = "security_q",
        ttl_days: int | None = None,
    ) -> None:
        """Encrypt *answer* with AES-GCM and upsert into security_answers table.

        Args:
            credential_id: The credential UUID (references credentials.id).
            question_hash: SHA-256 hex of the cache key (bank_id:credential_ref:field_key:normalized_question).
            answer: The plaintext answer — wiped from memory after encryption.
            field_key: Human-readable key identifying what is being asked (default ``"security_q"``).
            ttl_days: TTL in days. Defaults to ``BANCA_HUMAN_INPUT_TTL_DAYS`` env var or 90.

        Security: answer plaintext NEVER appears in logs or audit entries.
        """
        effective_ttl = (
            ttl_days
            if ttl_days is not None
            else int(
                os.environ.get("BANCA_HUMAN_INPUT_TTL_DAYS", str(_DEFAULT_SECURITY_ANSWER_TTL_DAYS))
            )
        )

        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)
        answer_bytes = answer.encode("utf-8")
        aad = _make_security_q_aad(credential_id, question_hash)

        row_key = derive_row_key(bytes(self._master), salt)
        try:
            ciphertext = AESGCM(bytes(row_key)).encrypt(nonce, answer_bytes, aad)
        finally:
            wipe_row_key(row_key)

        # Zeroize plaintext bytes as best effort
        answer_ba = bytearray(answer_bytes)
        for i in range(len(answer_ba)):
            answer_ba[i] = 0

        kdf_meta = json.dumps(
            {
                "salt_hex": salt.hex(),
                "time_cost": 3,
                "memory_cost": 262_144,
                "parallelism": 4,
            }
        )
        now = datetime.now(UTC)
        expires_at = (now + timedelta(days=effective_ttl)).isoformat()

        conn = self._pool.get()
        conn.execute(
            """
            INSERT INTO security_answers
                (credential_id, question_hash, field_key, ciphertext, nonce, kdf_meta,
                 created_at, expires_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(credential_id, question_hash) DO UPDATE SET
                field_key    = excluded.field_key,
                ciphertext   = excluded.ciphertext,
                nonce        = excluded.nonce,
                kdf_meta     = excluded.kdf_meta,
                created_at   = excluded.created_at,
                expires_at   = excluded.expires_at,
                last_used_at = NULL
            """,
            (
                credential_id,
                question_hash,
                field_key,
                ciphertext,
                nonce,
                kdf_meta,
                now.isoformat(),
                expires_at,
            ),
        )
        conn.commit()

        self._audit(
            "security_q_stored",
            "security_answer",
            f"{credential_id}:{question_hash[:8]}",
            {
                "field_key": field_key,
                "ttl_days": effective_ttl,
                "question_hash_prefix": question_hash[:8],
            },
        )

    def fetch_security_answer(self, credential_id: str, question_hash: str) -> str | None:
        """Decrypt and return the cached answer for *question_hash*, or ``None`` on miss.

        Checks TTL: if expires_at < now, treats as miss (does not delete — let purge handle it).
        Updates ``last_used_at`` on hit.

        Returns:
            Plaintext answer string on cache hit, or ``None`` on miss / expiry.

        Security: answer plaintext NEVER appears in logs or audit entries.
        """
        conn = self._pool.get()
        row = conn.execute(
            """
            SELECT field_key, ciphertext, nonce, kdf_meta, expires_at
            FROM security_answers
            WHERE credential_id = ? AND question_hash = ?
            """,
            (credential_id, question_hash),
        ).fetchone()

        hit_or_miss = "miss"

        if row is None:
            self._audit(
                "security_q_lookup",
                "security_answer",
                f"{credential_id}:{question_hash[:8]}",
                {
                    "hit_or_miss": "miss",
                    "reason": "not_found",
                    "question_hash_prefix": question_hash[:8],
                },
            )
            return None

        field_key, ciphertext, nonce, kdf_meta_json, expires_at_str = row
        now = datetime.now(UTC)
        expires_at = datetime.fromisoformat(expires_at_str)

        if expires_at.tzinfo is None:
            # Ensure timezone-aware comparison
            expires_at = expires_at.replace(tzinfo=UTC)

        if now > expires_at:
            self._audit(
                "security_q_lookup",
                "security_answer",
                f"{credential_id}:{question_hash[:8]}",
                {
                    "hit_or_miss": "miss",
                    "reason": "expired",
                    "question_hash_prefix": question_hash[:8],
                },
            )
            return None

        kdf_meta: dict[str, Any] = json.loads(kdf_meta_json)
        salt = bytes.fromhex(kdf_meta["salt_hex"])
        aad = _make_security_q_aad(credential_id, question_hash)

        row_key = derive_row_key(bytes(self._master), salt)
        try:
            plaintext_bytes = AESGCM(bytes(row_key)).decrypt(nonce, bytes(ciphertext), aad)
        finally:
            wipe_row_key(row_key)

        plaintext = plaintext_bytes.decode("utf-8")

        # Update last_used_at
        try:
            conn.execute(
                "UPDATE security_answers SET last_used_at = ? WHERE credential_id = ? AND question_hash = ?",
                (now.isoformat(), credential_id, question_hash),
            )
            conn.commit()
        except Exception:
            logger.warning("vault: failed to update last_used_at for security_q lookup")

        hit_or_miss = "hit"
        self._audit(
            "security_q_lookup",
            "security_answer",
            f"{credential_id}:{question_hash[:8]}",
            {
                "hit_or_miss": hit_or_miss,
                "field_key": field_key,
                "question_hash_prefix": question_hash[:8],
            },
        )
        return plaintext

    def invalidate_security_answer(
        self, credential_id: str, question_hash: str, reason: str = "manual"
    ) -> None:
        """Delete a cached security answer and record a tombstone audit entry.

        Args:
            credential_id: The credential UUID.
            question_hash: SHA-256 hex of the cache key.
            reason: Human-readable reason (e.g. ``"assertion_failed"``, ``"manual"``).
        """
        conn = self._pool.get()
        conn.execute(
            "DELETE FROM security_answers WHERE credential_id = ? AND question_hash = ?",
            (credential_id, question_hash),
        )
        conn.commit()

        self._audit(
            "security_q_invalidated",
            "security_answer",
            f"{credential_id}:{question_hash[:8]}",
            {"reason": reason, "question_hash_prefix": question_hash[:8]},
        )

    def list_security_questions(self, credential_id: str) -> list[QuestionMeta]:
        """Return metadata for all cached security answers for *credential_id* — no plaintext.

        Only returns non-expired entries.
        """
        conn = self._pool.get()
        now = datetime.now(UTC).isoformat()
        rows = conn.execute(
            """
            SELECT credential_id, question_hash, field_key, created_at, expires_at, last_used_at
            FROM security_answers
            WHERE credential_id = ? AND expires_at > ?
            ORDER BY created_at
            """,
            (credential_id, now),
        ).fetchall()
        return [
            QuestionMeta(
                credential_id=r[0],
                question_hash=r[1],
                field_key=r[2],
                created_at=r[3],
                expires_at=r[4],
                last_used_at=r[5],
            )
            for r in rows
        ]

    def purge_expired_security_answers(self) -> int:
        """Delete all expired security_answers rows.

        Returns:
            Count of deleted rows.
        """
        conn = self._pool.get()
        now = datetime.now(UTC).isoformat()
        cursor = conn.execute(
            "DELETE FROM security_answers WHERE expires_at <= ?",
            (now,),
        )
        count = cursor.rowcount
        conn.commit()
        self._audit(
            "security_q_purge",
            "security_answer",
            "all",
            {"count": count},
        )
        return count

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

    def list_credentials(self) -> list[CredentialSummary]:
        """Return metadata for all stored credentials (no plaintext).

        Query-side helper used by the CLI ``list-credentials`` command.
        Returns a list of :class:`CredentialSummary` dataclasses with
        ``id``, ``bank``, ``label``, and ``created_at`` fields.

        This method is intentionally NOT part of ``SecretStorePort`` because
        listing without plaintext is a read-model / query-side concern and
        does not belong in the domain port.
        """
        conn = self._pool.get()
        rows = conn.execute(
            "SELECT id, bank, label, created_at FROM credentials ORDER BY created_at"
        ).fetchall()
        return [CredentialSummary(id=r[0], bank=r[1], label=r[2], created_at=r[3]) for r in rows]

    def close(self) -> None:
        """Zeroize the master passphrase buffer.

        Call on application shutdown or after the vault is no longer needed.
        Prefer using the context-manager protocol (``with SecretVault(...) as v:``)
        to ensure automatic cleanup.
        """
        wipe_row_key(self._master)

    def __enter__(self) -> SecretVault:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# Make SecretVault a recognized implementation of SecretStorePort at import time
assert issubclass(SecretVault, SecretStorePort)  # type: ignore[misc]
