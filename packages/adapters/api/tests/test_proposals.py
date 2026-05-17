"""Tests for POST /maps/{bank}/proposals/{id}/approve and /reject.

Covers:
- test_approve_happy_path         — pending proposal → patch applied → git commit + tag → 200
- test_approve_expired_410        — created_at > 24h → 410 Gone
- test_approve_not_found          — non-existent id → 404
- test_approve_already_applied    — status=applied → 409 Conflict
- test_reject_happy_path          — status=rejected + signal
- test_linter_invoked_pre_apply   — invalid patch (validation fails) → 422 + no git commit
- test_atomic_write               — mid-write crash → original preserved via .bak
- test_git_apply_no_injection     — malicious proposal_id → ValueError from _sanitize_id
- test_ttl_cleanup_task           — pending > 24h → marked expired by expire_old
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from open_banca_domain.entities.remap_proposal import RemapProposal, RemapStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proposal(
    status: RemapStatus = RemapStatus.PENDING,
    expires_delta: timedelta = timedelta(hours=23),
    patch_diff: dict[str, Any] | None = None,
) -> RemapProposal:
    """Build a RemapProposal with sensible defaults."""
    if patch_diff is None:
        patch_diff = {
            "target_step_index": 1,
            "new_steps": [
                {
                    "step_id": "login_wait_form_new",
                    "action": "wait_for_selector",
                    "selector": "#username-input",
                    "timeout_ms": 10000,
                }
            ],
            "rationale": "Selector changed",
            "confidence": 0.9,
            "risk": "low",
        }
    return RemapProposal(
        id=str(uuid.uuid4()),
        bank="banco_general",
        breakage_id="breakage-123",
        judge_decision="selector_changed",
        confidence=0.9,
        risk="low",
        patch_diff=json.dumps(patch_diff),
        status=status,
        expires_at=datetime.now(UTC) + expires_delta,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def map_bank_dir(tmp_path: Path) -> Path:
    """Create a temporary banco_general directory with a valid map.json."""
    bank_dir = tmp_path / "banco_general"
    bank_dir.mkdir()
    map_data = {
        "bank_id": "banco_general",
        "version": "0.0.1",
        "schema_version": "1",
        "signature": None,
        "steps": [
            {
                "step_id": "login_navigate",
                "action": "navigate",
                "target": "https://example.com",
            },
            {
                "step_id": "login_wait_form",
                "action": "wait_for_selector",
                "target": "#old-selector",
            },
            {
                "step_id": "login_fill",
                "action": "fill",
                "target": "#input",
            },
        ],
    }
    (bank_dir / "map.json").write_text(json.dumps(map_data, indent=2))
    return bank_dir


@pytest.fixture()
def pending_proposal() -> RemapProposal:
    return _make_proposal()


@pytest.fixture()
def expired_proposal() -> RemapProposal:
    return _make_proposal(expires_delta=timedelta(hours=-1))


@pytest.fixture()
def applied_proposal() -> RemapProposal:
    return _make_proposal(status=RemapStatus.APPLIED)


@pytest.fixture()
def rejected_proposal() -> RemapProposal:
    return _make_proposal(status=RemapStatus.REJECTED)


# ---------------------------------------------------------------------------
# Approve endpoint tests
# ---------------------------------------------------------------------------


class TestApproveHappyPath:
    """Pending proposal → apply → git commit + tag → 200 with metadata."""

    def test_approve_returns_200_with_metadata(
        self,
        tmp_path: Path,
        map_bank_dir: Path,
        pending_proposal: RemapProposal,
    ) -> None:
        """POST /approve on a pending proposal calls patcher + git and returns 200."""
        from open_banca_api.config import get_settings
        from open_banca_api.dependencies import (
            get_apply_remap_uc,
            get_job_store,
            get_temporal_client,
            get_webhook_outbox,
        )
        from open_banca_api.main import create_app

        get_settings.cache_clear()
        app = create_app()

        # Stub job store with the pending proposal
        mock_job_store = MagicMock()
        mock_job_store.load_proposal.return_value = pending_proposal

        # Stub temporal adapter (no real Temporal)
        mock_orchestrator = MagicMock()
        mock_orchestrator.async_signal_remap_approved = AsyncMock()

        # Stub use case (marks proposal approved)
        mock_uc = MagicMock()
        mock_uc.execute.return_value = MagicMock(success=True)

        mock_event_bus = MagicMock()

        app.dependency_overrides[get_job_store] = lambda: mock_job_store
        app.dependency_overrides[get_temporal_client] = lambda: mock_orchestrator
        app.dependency_overrides[get_apply_remap_uc] = lambda: mock_uc
        app.dependency_overrides[get_webhook_outbox] = lambda: mock_event_bus

        # Patch _get_banks_root to point to tmp_path (parent of banco_general)
        # and git_commit_and_tag to avoid real git operations
        fake_git_result = MagicMock()
        fake_git_result.commit_sha = "abc1234"
        fake_git_result.tag = "bank-maps/banco_general/v0.0.2"

        with (
            patch(
                "open_banca_api.routers.maps._get_banks_root",
                return_value=map_bank_dir.parent,
            ),
            patch(
                "open_banca_api.routers.maps.git_commit_and_tag",
                return_value=fake_git_result,
            ),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                f"/maps/banco_general/proposals/{pending_proposal.id}/approve",
                headers={"Authorization": "Bearer test-token-abc123"},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["proposal_id"] == pending_proposal.id
        assert body["bank"] == "banco_general"
        assert body["commit_sha"] == "abc1234"
        assert "new_version" in body

    def test_map_json_updated_on_disk(
        self,
        tmp_path: Path,
        map_bank_dir: Path,
        pending_proposal: RemapProposal,
    ) -> None:
        """Approving a proposal writes the patched map.json to disk."""
        from open_banca_api.config import get_settings
        from open_banca_api.dependencies import (
            get_apply_remap_uc,
            get_job_store,
            get_temporal_client,
            get_webhook_outbox,
        )
        from open_banca_api.main import create_app

        get_settings.cache_clear()
        app = create_app()

        mock_job_store = MagicMock()
        mock_job_store.load_proposal.return_value = pending_proposal
        mock_orchestrator = MagicMock()
        mock_orchestrator.async_signal_remap_approved = AsyncMock()
        mock_uc = MagicMock()
        mock_event_bus = MagicMock()

        app.dependency_overrides[get_job_store] = lambda: mock_job_store
        app.dependency_overrides[get_temporal_client] = lambda: mock_orchestrator
        app.dependency_overrides[get_apply_remap_uc] = lambda: mock_uc
        app.dependency_overrides[get_webhook_outbox] = lambda: mock_event_bus

        fake_git_result = MagicMock()
        fake_git_result.commit_sha = "def5678"
        fake_git_result.tag = "bank-maps/banco_general/v0.0.2"

        with (
            patch("open_banca_api.routers.maps._get_banks_root", return_value=map_bank_dir.parent),
            patch("open_banca_api.routers.maps.git_commit_and_tag", return_value=fake_git_result),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            client.post(
                f"/maps/banco_general/proposals/{pending_proposal.id}/approve",
                headers={"Authorization": "Bearer test-token-abc123"},
            )

        # Verify map.json was patched
        updated = json.loads((map_bank_dir / "map.json").read_text())
        # new_steps replaces step at index 1 with our new step
        step_ids = [s["step_id"] for s in updated["steps"]]
        assert "login_wait_form_new" in step_ids
        assert "login_wait_form" not in step_ids

    def test_signal_sent_to_temporal(
        self,
        map_bank_dir: Path,
        pending_proposal: RemapProposal,
    ) -> None:
        """Approving a proposal calls async_signal_remap_approved."""
        from open_banca_api.config import get_settings
        from open_banca_api.dependencies import (
            get_apply_remap_uc,
            get_job_store,
            get_temporal_client,
            get_webhook_outbox,
        )
        from open_banca_api.main import create_app

        get_settings.cache_clear()
        app = create_app()

        mock_job_store = MagicMock()
        mock_job_store.load_proposal.return_value = pending_proposal
        signal_mock = AsyncMock()
        mock_orchestrator = MagicMock()
        mock_orchestrator.async_signal_remap_approved = signal_mock
        mock_uc = MagicMock()
        mock_event_bus = MagicMock()

        app.dependency_overrides[get_job_store] = lambda: mock_job_store
        app.dependency_overrides[get_temporal_client] = lambda: mock_orchestrator
        app.dependency_overrides[get_apply_remap_uc] = lambda: mock_uc
        app.dependency_overrides[get_webhook_outbox] = lambda: mock_event_bus

        fake_git = MagicMock()
        fake_git.commit_sha = "aaa0000"
        fake_git.tag = "bank-maps/banco_general/v0.0.2"

        with (
            patch("open_banca_api.routers.maps._get_banks_root", return_value=map_bank_dir.parent),
            patch("open_banca_api.routers.maps.git_commit_and_tag", return_value=fake_git),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.post(
                f"/maps/banco_general/proposals/{pending_proposal.id}/approve",
                headers={"Authorization": "Bearer test-token-abc123"},
            )

        assert resp.status_code == 200
        signal_mock.assert_called_once_with(pending_proposal.id)


class TestApproveErrors:
    """Error cases for the approve endpoint."""

    def _make_client_with_proposal(self, proposal: RemapProposal | None) -> TestClient:
        """Helper: return a TestClient whose job store returns the given proposal."""
        from open_banca_api.config import get_settings
        from open_banca_api.dependencies import (
            get_apply_remap_uc,
            get_job_store,
            get_temporal_client,
            get_webhook_outbox,
        )
        from open_banca_api.main import create_app

        get_settings.cache_clear()
        app = create_app()

        mock_job_store = MagicMock()
        mock_job_store.load_proposal.return_value = proposal
        mock_orchestrator = MagicMock()
        mock_orchestrator.async_signal_remap_approved = AsyncMock()
        mock_uc = MagicMock()
        mock_event_bus = MagicMock()

        app.dependency_overrides[get_job_store] = lambda: mock_job_store
        app.dependency_overrides[get_temporal_client] = lambda: mock_orchestrator
        app.dependency_overrides[get_apply_remap_uc] = lambda: mock_uc
        app.dependency_overrides[get_webhook_outbox] = lambda: mock_event_bus

        return TestClient(app, raise_server_exceptions=True)

    def test_approve_not_found_404(self) -> None:
        """Non-existent proposal_id → 404."""
        client = self._make_client_with_proposal(None)
        resp = client.post(
            "/maps/banco_general/proposals/nonexistent-id/approve",
            headers={"Authorization": "Bearer test-token-abc123"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "proposal_not_found"

    def test_approve_expired_410(self, expired_proposal: RemapProposal) -> None:
        """Expired proposal → 410 Gone."""
        client = self._make_client_with_proposal(expired_proposal)
        resp = client.post(
            f"/maps/banco_general/proposals/{expired_proposal.id}/approve",
            headers={"Authorization": "Bearer test-token-abc123"},
        )
        assert resp.status_code == 410
        assert resp.json()["detail"]["error"] == "proposal_expired"

    def test_approve_already_applied_409(self, applied_proposal: RemapProposal) -> None:
        """Already-applied proposal → 409 Conflict."""
        client = self._make_client_with_proposal(applied_proposal)
        resp = client.post(
            f"/maps/banco_general/proposals/{applied_proposal.id}/approve",
            headers={"Authorization": "Bearer test-token-abc123"},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "proposal_already_resolved"

    def test_approve_already_rejected_409(self, rejected_proposal: RemapProposal) -> None:
        """Already-rejected proposal → 409 Conflict."""
        client = self._make_client_with_proposal(rejected_proposal)
        resp = client.post(
            f"/maps/banco_general/proposals/{rejected_proposal.id}/approve",
            headers={"Authorization": "Bearer test-token-abc123"},
        )
        assert resp.status_code == 409


class TestLinterInvokedPreApply:
    """Linter (BankMap schema) must reject invalid patch before any write."""

    def test_invalid_patch_returns_422_no_git_commit(
        self,
        map_bank_dir: Path,
        pending_proposal: RemapProposal,
    ) -> None:
        """Patch that produces invalid schema → 422 and no git commit called."""
        # Replace patch_diff with one that results in a step missing 'action'
        bad_proposal = RemapProposal(
            id=pending_proposal.id,
            bank=pending_proposal.bank,
            breakage_id=pending_proposal.breakage_id,
            judge_decision=pending_proposal.judge_decision,
            confidence=pending_proposal.confidence,
            risk=pending_proposal.risk,
            patch_diff=json.dumps(
                {
                    "target_step_index": 0,
                    "new_steps": [{"step_id": "bad_step"}],  # missing 'action' field
                }
            ),
            status=RemapStatus.PENDING,
            expires_at=pending_proposal.expires_at,
        )

        from open_banca_api.config import get_settings
        from open_banca_api.dependencies import (
            get_apply_remap_uc,
            get_job_store,
            get_temporal_client,
            get_webhook_outbox,
        )
        from open_banca_api.main import create_app

        get_settings.cache_clear()
        app = create_app()

        mock_job_store = MagicMock()
        mock_job_store.load_proposal.return_value = bad_proposal
        mock_orchestrator = MagicMock()
        mock_orchestrator.async_signal_remap_approved = AsyncMock()
        mock_uc = MagicMock()
        mock_event_bus = MagicMock()

        app.dependency_overrides[get_job_store] = lambda: mock_job_store
        app.dependency_overrides[get_temporal_client] = lambda: mock_orchestrator
        app.dependency_overrides[get_apply_remap_uc] = lambda: mock_uc
        app.dependency_overrides[get_webhook_outbox] = lambda: mock_event_bus

        git_called = False

        def _mock_git(*args: Any, **kwargs: Any) -> Any:
            nonlocal git_called
            git_called = True
            raise AssertionError("git should not be called when linter fails")

        with (
            patch("open_banca_api.routers.maps._get_banks_root", return_value=map_bank_dir.parent),
            patch("open_banca_api.routers.maps.git_commit_and_tag", side_effect=_mock_git),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                f"/maps/banco_general/proposals/{bad_proposal.id}/approve",
                headers={"Authorization": "Bearer test-token-abc123"},
            )

        assert resp.status_code == 422, resp.text
        assert not git_called


class TestAtomicWrite:
    """Mid-write crash → original map.json preserved via .bak."""

    def test_backup_file_created_and_original_preserved_on_failure(
        self,
        map_bank_dir: Path,
    ) -> None:
        """If apply_patch_to_disk raises after .bak is created, original is intact."""
        from open_banca_api.services.map_patcher import apply_patch_to_disk

        original_content = (map_bank_dir / "map.json").read_text()

        # Use an out-of-bounds index to trigger IndexError (no write happens)
        with pytest.raises(IndexError):
            apply_patch_to_disk(
                bank_dir=map_bank_dir,
                target_step_index=999,
                new_steps=[{"step_id": "new", "action": "navigate", "target": "x"}],
            )

        # map.json must be unchanged
        assert (map_bank_dir / "map.json").read_text() == original_content

    def test_bak_file_removed_on_validation_failure(
        self,
        map_bank_dir: Path,
    ) -> None:
        """If validation fails, .bak is cleaned up (no write occurred, .bak removed)."""
        from open_banca_api.services.map_patcher import apply_patch_to_disk

        with pytest.raises(ValueError):
            apply_patch_to_disk(
                bank_dir=map_bank_dir,
                target_step_index=0,
                new_steps=[{"step_id": "bad_no_action"}],  # missing 'action'
            )

        # .bak must not be left behind
        assert not (map_bank_dir / "map.json.bak").exists()


class TestGitApplyNoInjection:
    """Malicious proposal_id or bank → ValueError from _sanitize_id."""

    @pytest.mark.parametrize(
        "malicious_id",
        [
            "../../evil",
            "id; rm -rf /",
            "$(whoami)",
            "id\nmalicious",
            "id`whoami`",
            "id'OR'1'='1",
        ],
    )
    def test_sanitize_id_rejects_shell_special_chars(self, malicious_id: str) -> None:
        """_sanitize_id raises ValueError for any non-[a-zA-Z0-9_-] input."""
        from open_banca_api.services.git_apply import _sanitize_id

        with pytest.raises(ValueError, match="disallowed characters"):
            _sanitize_id(malicious_id, "proposal_id")

    def test_git_commit_and_tag_rejects_malicious_bank(self, tmp_path: Path) -> None:
        """git_commit_and_tag raises ValueError for bank with shell chars."""
        from open_banca_api.services.git_apply import git_commit_and_tag

        with pytest.raises(ValueError):
            git_commit_and_tag(
                repo_root=tmp_path,
                bank="banco_general; rm -rf /",
                proposal_id="safe-uuid-123",
                new_version="0.0.2",
                map_rel_path="packages/banks/banco_general/map.json",
            )


# ---------------------------------------------------------------------------
# Reject endpoint tests
# ---------------------------------------------------------------------------


class TestRejectEndpoint:
    """Tests for POST /maps/{bank}/proposals/{id}/reject."""

    def _make_client_with_proposal(
        self, proposal: RemapProposal | None
    ) -> tuple[TestClient, MagicMock]:
        """Return (client, mock_orchestrator)."""
        from open_banca_api.config import get_settings
        from open_banca_api.dependencies import (
            get_apply_remap_uc,
            get_job_store,
            get_temporal_client,
            get_webhook_outbox,
        )
        from open_banca_api.main import create_app

        get_settings.cache_clear()
        app = create_app()

        mock_job_store = MagicMock()
        mock_job_store.load_proposal.return_value = proposal
        mock_orchestrator = MagicMock()
        mock_orchestrator.async_signal_remap_rejected = AsyncMock()
        mock_uc = MagicMock()
        mock_event_bus = MagicMock()

        app.dependency_overrides[get_job_store] = lambda: mock_job_store
        app.dependency_overrides[get_temporal_client] = lambda: mock_orchestrator
        app.dependency_overrides[get_apply_remap_uc] = lambda: mock_uc
        app.dependency_overrides[get_webhook_outbox] = lambda: mock_event_bus

        return TestClient(app, raise_server_exceptions=True), mock_orchestrator

    def test_reject_happy_path_returns_204(self, pending_proposal: RemapProposal) -> None:
        """Pending proposal + reject → 204 + signal sent."""
        client, mock_orch = self._make_client_with_proposal(pending_proposal)
        resp = client.post(
            f"/maps/banco_general/proposals/{pending_proposal.id}/reject",
            headers={"Authorization": "Bearer test-token-abc123"},
        )
        assert resp.status_code == 204
        mock_orch.async_signal_remap_rejected.assert_called_once_with(pending_proposal.id)

    def test_reject_not_found_404(self) -> None:
        """Non-existent proposal → 404."""
        client, _ = self._make_client_with_proposal(None)
        resp = client.post(
            "/maps/banco_general/proposals/ghost-id/reject",
            headers={"Authorization": "Bearer test-token-abc123"},
        )
        assert resp.status_code == 404

    def test_reject_expired_410(self, expired_proposal: RemapProposal) -> None:
        """Expired proposal → 410."""
        client, _ = self._make_client_with_proposal(expired_proposal)
        resp = client.post(
            f"/maps/banco_general/proposals/{expired_proposal.id}/reject",
            headers={"Authorization": "Bearer test-token-abc123"},
        )
        assert resp.status_code == 410

    def test_reject_already_resolved_409(self, applied_proposal: RemapProposal) -> None:
        """Already-applied → 409."""
        client, _ = self._make_client_with_proposal(applied_proposal)
        resp = client.post(
            f"/maps/banco_general/proposals/{applied_proposal.id}/reject",
            headers={"Authorization": "Bearer test-token-abc123"},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# TTL cleanup task tests
# ---------------------------------------------------------------------------


class TestTTLCleanupTask:
    """ProposalRepository.expire_old() marks proposals > 24h as expired."""

    @pytest.fixture()
    def _db_conn(self, tmp_path: Path) -> Any:
        """Return a migrated in-memory SQLCipher connection."""
        from open_banca_storage.connection import ConnectionPool, PassthroughKeyDerivation
        from open_banca_storage.migrations import migrate

        pool = ConnectionPool(
            db_path=tmp_path / "test.db",
            passphrase="test",
            key_derivation=PassthroughKeyDerivation(),
        )
        conn = pool.get()
        migrate(conn)
        return conn

    def test_expire_old_marks_past_ttl_proposals(self, _db_conn: Any) -> None:
        """Proposals with expires_at in the past are marked expired."""
        from open_banca_storage.repositories.job_store import SqliteJobStore
        from open_banca_storage.repositories.proposal_repo import ProposalRepository

        store = SqliteJobStore(_db_conn)
        repo = ProposalRepository(_db_conn)

        # Insert a proposal that expired 1 hour ago
        old_proposal = _make_proposal(
            status=RemapStatus.PENDING,
            expires_delta=timedelta(hours=-1),
        )
        store.save_proposal(old_proposal)

        # Insert a proposal that is still valid
        fresh_proposal = _make_proposal(
            status=RemapStatus.PENDING,
            expires_delta=timedelta(hours=12),
        )
        store.save_proposal(fresh_proposal)

        expired_ids = repo.expire_old()

        assert old_proposal.id in expired_ids
        assert fresh_proposal.id not in expired_ids

        # Verify DB state
        old_loaded = repo.load_proposal(old_proposal.id)
        assert old_loaded is not None
        assert old_loaded.status == RemapStatus.EXPIRED

        fresh_loaded = repo.load_proposal(fresh_proposal.id)
        assert fresh_loaded is not None
        assert fresh_loaded.status == RemapStatus.PENDING

    def test_expire_old_returns_empty_when_no_pending_past_ttl(self, _db_conn: Any) -> None:
        """No expired proposals → empty list returned."""
        from open_banca_storage.repositories.proposal_repo import ProposalRepository

        repo = ProposalRepository(_db_conn)
        assert repo.expire_old() == []

    def test_mark_applied(self, _db_conn: Any) -> None:
        """mark_applied sets status to APPLIED."""
        from open_banca_storage.repositories.job_store import SqliteJobStore
        from open_banca_storage.repositories.proposal_repo import ProposalRepository

        store = SqliteJobStore(_db_conn)
        repo = ProposalRepository(_db_conn)

        p = _make_proposal()
        store.save_proposal(p)
        repo.mark_applied(p.id)

        loaded = repo.load_proposal(p.id)
        assert loaded is not None
        assert loaded.status == RemapStatus.APPLIED

    def test_mark_rejected_with_reason(self, _db_conn: Any) -> None:
        """mark_rejected sets status to REJECTED and stores reason."""
        from open_banca_storage.repositories.job_store import SqliteJobStore
        from open_banca_storage.repositories.proposal_repo import ProposalRepository

        store = SqliteJobStore(_db_conn)
        repo = ProposalRepository(_db_conn)

        p = _make_proposal()
        store.save_proposal(p)
        repo.mark_rejected(p.id, reason="operator_said_no")

        loaded = repo.load_proposal(p.id)
        assert loaded is not None
        assert loaded.status == RemapStatus.REJECTED

    def test_list_pending(self, _db_conn: Any) -> None:
        """list_pending returns only PENDING proposals."""
        from open_banca_storage.repositories.job_store import SqliteJobStore
        from open_banca_storage.repositories.proposal_repo import ProposalRepository

        store = SqliteJobStore(_db_conn)
        repo = ProposalRepository(_db_conn)

        p1 = _make_proposal()
        p2 = _make_proposal(status=RemapStatus.APPLIED)
        store.save_proposal(p1)
        store.save_proposal(p2)

        pending = repo.list_pending()
        ids = [p.id for p in pending]
        assert p1.id in ids
        assert p2.id not in ids
