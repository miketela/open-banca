"""Tests for SecretVault security_q namespace (ADR-0021).

TDD coverage:
1. store + fetch roundtrip — decrypted answer matches original.
2. TTL expire — fetch returns None after expiry.
3. Invalidation roundtrip — answer deleted, subsequent fetch returns None.
4. list_security_questions — returns metadata without plaintext.
5. purge_expired — deletes only expired entries.
6. Upsert — storing same key twice overwrites.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from open_banca_storage.connection import ConnectionPool, PassthroughKeyDerivation
from open_banca_storage.migrations import migrate
from open_banca_storage.secret_vault import QuestionMeta, SecretVault

PASSPHRASE = "test-passphrase-vault-security"


@pytest.fixture()
def vault(tmp_path: Path) -> SecretVault:
    """Provision a SecretVault backed by a temp in-memory-style SQLite DB."""
    db_file = tmp_path / "test_security_q.db"
    kdf = PassthroughKeyDerivation()
    pool = ConnectionPool(db_path=db_file, passphrase=PASSPHRASE, key_derivation=kdf)
    conn = pool.get()
    migrate(conn)
    return SecretVault(pool=pool, master_passphrase=PASSPHRASE)


# ── 1. store + fetch roundtrip ────────────────────────────────────────────────


def test_store_and_fetch_roundtrip(vault: SecretVault) -> None:
    """Store an answer, then fetch it — plaintext must match."""
    vault.store_security_answer(
        credential_id="cred-001",
        question_hash="deadbeef" * 8,
        answer="rojo",
        field_key="security_q_mother_color",
    )
    result = vault.fetch_security_answer("cred-001", "deadbeef" * 8)
    assert result == "rojo"


def test_fetch_miss_on_unknown_hash(vault: SecretVault) -> None:
    """Fetching a non-existent question hash returns None."""
    result = vault.fetch_security_answer("cred-002", "0" * 64)
    assert result is None


# ── 2. TTL expire ─────────────────────────────────────────────────────────────


def test_fetch_returns_none_after_ttl_expiry(vault: SecretVault) -> None:
    """An entry with TTL=0 days (effectively expired) returns None on fetch."""
    # Store with ttl_days=0 → expires_at = created_at
    # We monkeypatch the expires_at after insertion to force expiry
    vault.store_security_answer(
        credential_id="cred-ttl",
        question_hash="aaaabbbb" * 8,
        answer="blue",
        field_key="security_q_pet",
        ttl_days=90,
    )

    # Manually expire the entry in the DB
    conn = vault._pool.get()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    conn.execute(
        "UPDATE security_answers SET expires_at = ? WHERE credential_id = ? AND question_hash = ?",
        (past, "cred-ttl", "aaaabbbb" * 8),
    )
    conn.commit()

    result = vault.fetch_security_answer("cred-ttl", "aaaabbbb" * 8)
    assert result is None, "Expired entry should return None"


# ── 3. Invalidation roundtrip ─────────────────────────────────────────────────


def test_invalidate_removes_entry(vault: SecretVault) -> None:
    """Invalidating an entry deletes it; subsequent fetch returns None."""
    vault.store_security_answer(
        credential_id="cred-inv",
        question_hash="cafecafe" * 8,
        answer="verde",
        field_key="security_q_color",
    )
    assert vault.fetch_security_answer("cred-inv", "cafecafe" * 8) == "verde"

    vault.invalidate_security_answer("cred-inv", "cafecafe" * 8, reason="assertion_failed")

    result = vault.fetch_security_answer("cred-inv", "cafecafe" * 8)
    assert result is None, "Invalidated entry should return None"


def test_invalidate_nonexistent_is_noop(vault: SecretVault) -> None:
    """Invalidating a non-existent hash raises no error."""
    vault.invalidate_security_answer("cred-ghost", "0" * 64, reason="test")


# ── 4. list_security_questions ────────────────────────────────────────────────


def test_list_security_questions_returns_metadata(vault: SecretVault) -> None:
    """list_security_questions returns QuestionMeta without plaintext."""
    vault.store_security_answer(
        credential_id="cred-list",
        question_hash="hash1" + "0" * 59,
        answer="secretA",
        field_key="security_q_first",
    )
    vault.store_security_answer(
        credential_id="cred-list",
        question_hash="hash2" + "0" * 59,
        answer="secretB",
        field_key="security_q_second",
    )

    results = vault.list_security_questions("cred-list")
    assert len(results) == 2
    assert all(isinstance(r, QuestionMeta) for r in results)
    field_keys = {r.field_key for r in results}
    assert field_keys == {"security_q_first", "security_q_second"}

    # Ensure no plaintext in metadata
    for meta in results:
        assert not hasattr(meta, "answer")
        assert not hasattr(meta, "plaintext")


def test_list_security_questions_empty(vault: SecretVault) -> None:
    """list_security_questions returns empty list for unknown credential."""
    results = vault.list_security_questions("cred-nobody")
    assert results == []


# ── 5. purge_expired ──────────────────────────────────────────────────────────


def test_purge_expired_removes_only_expired(vault: SecretVault) -> None:
    """purge_expired removes expired entries but keeps active ones."""
    vault.store_security_answer(
        credential_id="cred-purge",
        question_hash="active00" * 8,
        answer="stays",
        field_key="security_q_active",
        ttl_days=90,
    )
    vault.store_security_answer(
        credential_id="cred-purge",
        question_hash="expired0" * 8,
        answer="goes",
        field_key="security_q_expired",
        ttl_days=90,
    )

    # Manually expire the second entry
    conn = vault._pool.get()
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    conn.execute(
        "UPDATE security_answers SET expires_at = ? WHERE question_hash = ?",
        (past, "expired0" * 8),
    )
    conn.commit()

    count = vault.purge_expired_security_answers()
    assert count == 1

    # Active entry still fetchable
    assert vault.fetch_security_answer("cred-purge", "active00" * 8) == "stays"
    # Expired entry gone
    assert vault.fetch_security_answer("cred-purge", "expired0" * 8) is None


# ── 6. Upsert ────────────────────────────────────────────────────────────────


def test_upsert_overwrites_existing(vault: SecretVault) -> None:
    """Storing the same key twice overwrites the previous answer."""
    vault.store_security_answer(
        credential_id="cred-upsert",
        question_hash="upserth0" * 8,
        answer="first",
        field_key="security_q_thing",
    )
    vault.store_security_answer(
        credential_id="cred-upsert",
        question_hash="upserth0" * 8,
        answer="second",
        field_key="security_q_thing",
    )
    result = vault.fetch_security_answer("cred-upsert", "upserth0" * 8)
    assert result == "second"
