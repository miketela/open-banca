"""Unit tests for ProposalRepository.

Verifies save/load, mark_applied, mark_rejected, list_pending, expire_old.
Uses a real SQLCipher connection (in-memory via tmp_path) so we test actual SQL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from open_banca_domain.entities.remap_proposal import RemapProposal, RemapStatus


def _make_proposal(
    status: RemapStatus = RemapStatus.PENDING,
    expires_delta: timedelta = timedelta(hours=23),
) -> RemapProposal:
    return RemapProposal(
        id=str(uuid.uuid4()),
        bank="banco_general",
        breakage_id=str(uuid.uuid4()),
        judge_decision="selector_changed",
        confidence=0.88,
        risk="low",
        patch_diff='{"target_step_index": 0, "new_steps": []}',
        status=status,
        expires_at=datetime.now(UTC) + expires_delta,
    )


@pytest.fixture()
def conn(tmp_path: Path) -> Any:
    """Migrated SQLCipher connection on a temp file."""
    from open_banca_storage.connection import ConnectionPool, PassthroughKeyDerivation
    from open_banca_storage.migrations import migrate

    pool = ConnectionPool(
        db_path=tmp_path / "test.db",
        passphrase="test-passphrase",
        key_derivation=PassthroughKeyDerivation(),
    )
    c = pool.get()
    migrate(c)
    return c


@pytest.fixture()
def store(conn: Any) -> Any:
    """SqliteJobStore for inserting proposals."""
    from open_banca_storage.repositories.job_store import SqliteJobStore

    return SqliteJobStore(conn)


@pytest.fixture()
def repo(conn: Any) -> Any:
    """ProposalRepository under test."""
    from open_banca_storage.repositories.proposal_repo import ProposalRepository

    return ProposalRepository(conn)


class TestSaveAndLoad:
    def test_load_returns_none_for_unknown_id(self, repo: Any) -> None:
        assert repo.load_proposal("unknown") is None

    def test_save_and_load_roundtrip(self, store: Any, repo: Any) -> None:
        p = _make_proposal()
        store.save_proposal(p)
        loaded = repo.load_proposal(p.id)
        assert loaded is not None
        assert loaded.id == p.id
        assert loaded.status == RemapStatus.PENDING


class TestMarkApplied:
    def test_mark_applied_sets_status(self, store: Any, repo: Any) -> None:
        p = _make_proposal()
        store.save_proposal(p)
        repo.mark_applied(p.id)
        loaded = repo.load_proposal(p.id)
        assert loaded is not None
        assert loaded.status == RemapStatus.APPLIED

    def test_mark_applied_noop_for_unknown(self, repo: Any) -> None:
        """No exception when marking an unknown ID."""
        repo.mark_applied("ghost-id")  # must not raise


class TestMarkRejected:
    def test_mark_rejected_sets_status(self, store: Any, repo: Any) -> None:
        p = _make_proposal()
        store.save_proposal(p)
        repo.mark_rejected(p.id)
        loaded = repo.load_proposal(p.id)
        assert loaded is not None
        assert loaded.status == RemapStatus.REJECTED

    def test_mark_rejected_with_reason_stores_reason(self, store: Any, repo: Any) -> None:
        p = _make_proposal()
        store.save_proposal(p)
        repo.mark_rejected(p.id, reason="bad_confidence")
        loaded = repo.load_proposal(p.id)
        # judge_decision is reused as reason storage
        assert loaded is not None
        assert loaded.status == RemapStatus.REJECTED


class TestListPending:
    def test_list_pending_returns_only_pending(self, store: Any, repo: Any) -> None:
        p1 = _make_proposal(status=RemapStatus.PENDING)
        p2 = _make_proposal(status=RemapStatus.APPLIED)
        p3 = _make_proposal(status=RemapStatus.REJECTED)
        store.save_proposal(p1)
        store.save_proposal(p2)
        store.save_proposal(p3)

        pending = repo.list_pending()
        ids = [p.id for p in pending]
        assert p1.id in ids
        assert p2.id not in ids
        assert p3.id not in ids

    def test_list_pending_empty_when_none(self, repo: Any) -> None:
        assert repo.list_pending() == []


class TestExpireOld:
    def test_expire_old_marks_past_ttl_pending(self, store: Any, repo: Any) -> None:
        p_old = _make_proposal(expires_delta=timedelta(hours=-1))
        p_fresh = _make_proposal(expires_delta=timedelta(hours=10))
        store.save_proposal(p_old)
        store.save_proposal(p_fresh)

        expired = repo.expire_old()

        assert p_old.id in expired
        assert p_fresh.id not in expired

        assert repo.load_proposal(p_old.id).status == RemapStatus.EXPIRED  # type: ignore[union-attr]
        assert repo.load_proposal(p_fresh.id).status == RemapStatus.PENDING  # type: ignore[union-attr]

    def test_expire_old_does_not_expire_applied(self, store: Any, repo: Any) -> None:
        """Already-applied proposals past TTL are not re-expired."""
        p = _make_proposal(status=RemapStatus.APPLIED, expires_delta=timedelta(hours=-2))
        store.save_proposal(p)

        expired = repo.expire_old()
        assert p.id not in expired

    def test_expire_old_returns_empty_when_nothing_due(self, repo: Any) -> None:
        assert repo.expire_old() == []
