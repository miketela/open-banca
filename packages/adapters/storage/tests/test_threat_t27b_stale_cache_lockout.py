"""Tests for T27b — stale cache lockout defense (ADR-0021).

Scenario: a cached security answer becomes stale (bank changed it, or was
poisoned by a wrong answer). Repeated use of the stale answer could cause
bank to lock the account (T04 blast radius).

Mitigation:
  - TTL 90 days (already tested in test_vault_security_answers.py).
  - Hard invalidation on downstream failure (workflow side — asserted here).
  - Cap 3 attempts per field_key per session (API rate limit — asserted in API tests).

TDD coverage (T27b specific):
1. After vault invalidation, subsequent fetch returns None (no reuse).
2. invalidate_security_answer with reason='assertion_failed' records audit.
3. Multiple store → invalidate → store cycles work correctly (no residue).
4. Invalidation of unknown hash is a no-op (safe).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from open_banca_storage.connection import ConnectionPool, PassthroughKeyDerivation
from open_banca_storage.migrations import migrate
from open_banca_storage.secret_vault import SecretVault

PASSPHRASE = "test-t27b-passphrase"


@pytest.fixture()
def vault(tmp_path: Path) -> SecretVault:
    db_file = tmp_path / "test_t27b.db"
    kdf = PassthroughKeyDerivation()
    pool = ConnectionPool(db_path=db_file, passphrase=PASSPHRASE, key_derivation=kdf)
    conn = pool.get()
    migrate(conn)
    return SecretVault(pool=pool, master_passphrase=PASSPHRASE)


# ── 1. Invalidation prevents reuse ───────────────────────────────────────────


def test_invalidated_answer_not_reused(vault: SecretVault) -> None:
    """After invalidation, fetch returns None — stale answer cannot be reused."""
    q_hash = "stale_hash" + "0" * 54

    vault.store_security_answer(
        credential_id="cred-t27b",
        question_hash=q_hash,
        answer="wrong_answer",
        field_key="security_q_pet",
    )
    # Simulate assertion_failed downstream — invalidate
    vault.invalidate_security_answer(
        credential_id="cred-t27b",
        question_hash=q_hash,
        reason="assertion_failed",
    )

    # Fetch must return None — cannot reuse stale answer
    result = vault.fetch_security_answer("cred-t27b", q_hash)
    assert result is None, "T27b: stale answer must not be reusable after invalidation"


# ── 2. Invalidation reason is recorded ───────────────────────────────────────


def test_invalidation_records_audit_with_reason(vault: SecretVault) -> None:
    """invalidate_security_answer with reason='assertion_failed' writes audit log."""
    q_hash = "audit_hash" + "0" * 53

    vault.store_security_answer(
        credential_id="cred-audit",
        question_hash=q_hash,
        answer="wrong",
        field_key="security_q_color",
    )
    # Should not raise
    vault.invalidate_security_answer(
        credential_id="cred-audit",
        question_hash=q_hash,
        reason="assertion_failed",
    )

    # Verify the audit log contains the invalidation entry
    conn = vault._pool.get()
    rows = conn.execute(
        "SELECT action, metadata_json FROM audit_log WHERE action = 'security_q_invalidated' "
        "ORDER BY at DESC LIMIT 1"
    ).fetchall()
    assert len(rows) >= 1, "Expected audit log entry for security_q_invalidated"
    _action, meta_json = rows[0]
    import json

    meta = json.loads(meta_json)
    assert meta.get("reason") == "assertion_failed"


# ── 3. Multiple store → invalidate → store cycles ────────────────────────────


def test_reinstate_after_invalidation(vault: SecretVault) -> None:
    """After invalidation, a new correct answer can be stored and fetched."""
    q_hash = "cycle_hash" + "0" * 53

    # First cycle: store wrong answer, invalidate
    vault.store_security_answer(
        credential_id="cred-cycle",
        question_hash=q_hash,
        answer="wrong",
        field_key="security_q_pet",
    )
    vault.invalidate_security_answer("cred-cycle", q_hash, reason="assertion_failed")
    assert vault.fetch_security_answer("cred-cycle", q_hash) is None

    # Second cycle: store correct answer, fetch
    vault.store_security_answer(
        credential_id="cred-cycle",
        question_hash=q_hash,
        answer="correct",
        field_key="security_q_pet",
    )
    result = vault.fetch_security_answer("cred-cycle", q_hash)
    assert result == "correct", "Re-stored answer should be fetchable"


# ── 4. Invalidation of unknown hash is safe ───────────────────────────────────


def test_invalidate_unknown_hash_is_noop(vault: SecretVault) -> None:
    """Invalidating a non-existent question hash must not raise."""
    # Should not raise
    vault.invalidate_security_answer(
        credential_id="cred-ghost",
        question_hash="unknownhash" + "0" * 53,
        reason="circuit_breaker",
    )


# ── 5. Circuit breaker: 3 failures lead to invalidation ──────────────────────


def test_three_failures_pattern_invalidates(vault: SecretVault) -> None:
    """Simulates the T27b pattern: 3 consecutive failures → invalidate.

    This test asserts the BEHAVIOR contract: after 3 external signals of failure,
    the vault answer is gone. The circuit breaker logic lives in the workflow/
    activity layer, but we verify the vault supports the invalidation call.
    """
    q_hash = "t27b_3fail" + "0" * 53

    vault.store_security_answer(
        credential_id="cred-3fail",
        question_hash=q_hash,
        answer="maybe_wrong",
        field_key="security_q_thing",
    )

    # Simulate 3 consecutive failures triggering invalidation
    for i in range(3):
        # Vault still has the answer during failures
        assert vault.fetch_security_answer("cred-3fail", q_hash) is not None
        # On 3rd failure, external code invalidates
        if i == 2:
            vault.invalidate_security_answer("cred-3fail", q_hash, reason="t27b_three_strikes")

    # After invalidation: no more cache
    final = vault.fetch_security_answer("cred-3fail", q_hash)
    assert final is None, "T27b: after 3 failures, answer must be invalidated"
