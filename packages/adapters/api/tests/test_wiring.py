"""Tests for task #31 — API endpoint wiring to use cases and Temporal client.

Strategy:
- Use FastAPI dependency_overrides to inject fakes for Temporal and storage.
- No real Temporal server required; all Temporal calls go through mocks.
- Tests verify correct use-case invocation and HTTP semantics.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

# Set env vars before importing app
os.environ.setdefault("OPEN_BANCA_API_TOKEN", "test-token-abc123")
os.environ.setdefault("OPEN_BANCA_DB_PATH", ":memory:")

from open_banca_api.config import get_settings  # noqa: E402
from open_banca_api.dependencies import (  # noqa: E402
    get_job_store,
    get_temporal_client,
    get_webhook_outbox,
)
from open_banca_api.main import create_app  # noqa: E402
from open_banca_domain.entities.job import Job, JobMode, JobStatus  # noqa: E402
from open_banca_domain.entities.remap_proposal import RemapProposal, RemapStatus  # noqa: E402
from open_banca_domain.entities.transaction import Transaction  # noqa: E402

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _make_job(
    job_id: str | None = None,
    status: JobStatus = JobStatus.PENDING,
    bank: str = "banco_general",
) -> Job:
    jid = job_id or str(uuid4())
    now = _now()
    return Job(
        id=jid,
        status=status,
        bank=bank,
        credential_ref="cred-ref",
        mode=JobMode.FULL,
        created_at=now,
        updated_at=now,
    )


def _make_proposal(
    proposal_id: str | None = None,
    status: RemapStatus = RemapStatus.PENDING,
    expires_future: bool = True,
) -> RemapProposal:
    pid = proposal_id or str(uuid4())
    expires = _now() + timedelta(hours=24) if expires_future else _now() - timedelta(hours=1)
    return RemapProposal(
        id=pid,
        bank="banco_general",
        breakage_id=str(uuid4()),
        judge_decision="approve",
        confidence=0.95,
        risk="low",
        patch_diff='{"op": "replace", "path": "/steps/0/target"}',
        status=status,
        expires_at=expires,
    )


class FakeJobStore:
    """In-memory job store for testing."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._proposals: dict[str, RemapProposal] = {}
        self._idempotency: dict[str, dict[str, str]] = {}
        self._accounts: list[Any] = []
        self._transactions: list[Transaction] = []

    def save_job(self, job: Job) -> None:
        self._jobs[job.id] = job

    def load_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        return list(self._jobs.values())

    def save_account(self, account: Any) -> None:
        self._accounts.append(account)

    def save_transaction(self, tx: Transaction) -> None:
        self._transactions.append(tx)

    def get_cursor(self, bank: str) -> str | None:
        return None

    def save_cursor(self, bank: str, cursor: str) -> None:
        pass

    def list_accounts_by_bank(self, bank: str) -> list[Any]:
        return list(self._accounts)

    def list_transactions_by_job(self, job_id: str) -> list[Transaction]:
        return list(self._transactions)

    def save_proposal(self, proposal: RemapProposal) -> None:
        self._proposals[proposal.id] = proposal

    def load_proposal(self, proposal_id: str) -> RemapProposal | None:
        return self._proposals.get(proposal_id)

    def get_idempotency(self, key: str) -> dict[str, str] | None:
        return self._idempotency.get(key)

    def save_idempotency(self, key: str, request_hash: str, job_id: str) -> None:
        self._idempotency[key] = {"key": key, "request_hash": request_hash, "job_id": job_id}


class FakeTemporalAdapter:
    """Fake Temporal adapter for testing — records calls.

    Implements both the sync OrchestratorPort interface and the async helper
    methods used by endpoints directly.
    """

    def __init__(self) -> None:
        self.started_workflows: list[dict[str, Any]] = []
        self.otp_signals: list[str] = []
        self.remap_signals: list[str] = []
        self.cancellations: list[str] = []
        self.status_responses: dict[str, str] = {}

    # ── OrchestratorPort sync interface ─────────────────────────────────────

    def start_job(self, bank: str, credential_ref: str, mode: str) -> str:
        job_id = str(uuid4())
        self.started_workflows.append({"bank": bank, "mode": mode})
        return job_id

    def signal_otp_confirmed(self, job_id: str) -> None:
        self.otp_signals.append(job_id)

    def signal_remap_approved(self, proposal_id: str) -> None:
        self.remap_signals.append(proposal_id)

    def cancel_job(self, job_id: str) -> None:
        self.cancellations.append(job_id)

    def query_status(self, job_id: str) -> str:
        return self.status_responses.get(job_id, "running")

    # ── Async helpers used by endpoints ──────────────────────────────────────

    async def async_start_workflow(
        self,
        job_id: str,
        bank_id: str,
        credential_ref: str,
        mode: str,
        since_cursor: str | None,
        account_filter: list[str] | None,
    ) -> None:
        self.started_workflows.append(
            {
                "job_id": job_id,
                "bank_id": bank_id,
                "credential_ref": credential_ref,
                "mode": mode,
                "since_cursor": since_cursor,
                "account_filter": account_filter,
            }
        )

    async def async_signal_otp_confirmed(self, job_id: str) -> None:
        self.otp_signals.append(job_id)

    async def async_signal_remap_approved(self, proposal_id: str) -> None:
        self.remap_signals.append(proposal_id)

    async def async_cancel_job(self, job_id: str) -> None:
        self.cancellations.append(job_id)

    async def async_query_status(self, job_id: str) -> str:
        return self.status_responses.get(job_id, "running")


class FakeEventBus:
    """Fake webhook outbox event bus."""

    def __init__(self) -> None:
        self.published: list[Any] = []

    def publish(self, event: Any) -> None:
        self.published.append(event)

    def retry_pending(self) -> None:
        pass


# ---------------------------------------------------------------------------
# App fixture with overrides
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_store() -> FakeJobStore:
    return FakeJobStore()


@pytest.fixture()
def fake_temporal() -> FakeTemporalAdapter:
    return FakeTemporalAdapter()


@pytest.fixture()
def fake_event_bus() -> FakeEventBus:
    return FakeEventBus()


@pytest.fixture()
def wired_client(
    fake_store: FakeJobStore,
    fake_temporal: FakeTemporalAdapter,
    fake_event_bus: FakeEventBus,
) -> TestClient:
    """TestClient with all dependencies overridden to fakes."""
    get_settings.cache_clear()
    app = create_app()

    app.dependency_overrides[get_job_store] = lambda: fake_store
    app.dependency_overrides[get_temporal_client] = lambda: fake_temporal
    app.dependency_overrides[get_webhook_outbox] = lambda: fake_event_bus

    return TestClient(app, raise_server_exceptions=True)


AUTH = {"Authorization": "Bearer test-token-abc123"}


# ---------------------------------------------------------------------------
# POST /scrape
# ---------------------------------------------------------------------------


class TestPostScrape:
    def test_post_scrape_starts_workflow(
        self,
        wired_client: TestClient,
        fake_temporal: FakeTemporalAdapter,
        fake_store: FakeJobStore,
    ) -> None:
        """POST /scrape must start a Temporal workflow and persist the job."""
        resp = wired_client.post(
            "/scrape",
            json={"bank_id": "banco_general", "credentials": "enc-blob", "full": True},
            headers=AUTH,
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert "job_id" in body
        assert body["status"] == "pending"

        # Workflow was started
        assert len(fake_temporal.started_workflows) == 1
        wf = fake_temporal.started_workflows[0]
        assert wf["bank_id"] == "banco_general"
        assert wf["mode"] == "full"

        # Job persisted in store
        job = fake_store.load_job(body["job_id"])
        assert job is not None
        assert str(job.status) == "pending"

    def test_post_scrape_idempotency_same_key_same_body(
        self,
        wired_client: TestClient,
        fake_temporal: FakeTemporalAdapter,
    ) -> None:
        """Same Idempotency-Key + same payload → returns same job_id."""
        body = {"bank_id": "banco_general", "credentials": "enc-blob"}
        headers = {**AUTH, "Idempotency-Key": "idem-key-001"}

        resp1 = wired_client.post("/scrape", json=body, headers=headers)
        resp2 = wired_client.post("/scrape", json=body, headers=headers)

        assert resp1.status_code == 202
        assert resp2.status_code in (200, 202)
        assert resp1.json()["job_id"] == resp2.json()["job_id"]
        # Only one workflow started
        assert len(fake_temporal.started_workflows) == 1

    def test_post_scrape_idempotency_same_key_different_body(
        self,
        wired_client: TestClient,
    ) -> None:
        """Same Idempotency-Key + different payload → 409 Conflict."""
        headers = {**AUTH, "Idempotency-Key": "idem-key-conflict"}
        body1 = {"bank_id": "banco_general", "credentials": "enc-blob-A"}
        body2 = {"bank_id": "banco_general", "credentials": "enc-blob-B"}

        resp1 = wired_client.post("/scrape", json=body1, headers=headers)
        resp2 = wired_client.post("/scrape", json=body2, headers=headers)

        assert resp1.status_code == 202
        assert resp2.status_code == 409

    def test_post_scrape_idempotency_persisted(
        self,
        wired_client: TestClient,
        fake_store: FakeJobStore,
    ) -> None:
        """Idempotency record is persisted in storage (not only in-memory)."""
        body = {"bank_id": "banco_general", "credentials": "enc-blob"}
        key = "idem-persisted-001"
        headers = {**AUTH, "Idempotency-Key": key}

        wired_client.post("/scrape", json=body, headers=headers)

        # Check storage
        record = fake_store.get_idempotency(key)
        assert record is not None
        assert "job_id" in record
        assert "request_hash" in record


# ---------------------------------------------------------------------------
# GET /jobs/{id}
# ---------------------------------------------------------------------------


class TestGetJob:
    def test_get_jobs_returns_status(
        self,
        wired_client: TestClient,
        fake_store: FakeJobStore,
        fake_temporal: FakeTemporalAdapter,
    ) -> None:
        """GET /jobs/{id} returns job metadata and temporal status."""
        job = _make_job(status=JobStatus.RUNNING)
        fake_store.save_job(job)
        fake_temporal.status_responses[job.id] = "running"

        resp = wired_client.get(f"/jobs/{job.id}", headers=AUTH)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["job_id"] == job.id
        assert body["status"] == "running"
        assert body["temporal_status"] == "running"

    def test_get_job_404_unknown(
        self,
        wired_client: TestClient,
    ) -> None:
        """GET /jobs/{id} returns 404 for unknown job."""
        resp = wired_client.get("/jobs/nonexistent-id", headers=AUTH)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /jobs/{id}/otp-confirmed
# ---------------------------------------------------------------------------


class TestOTPConfirmed:
    def test_otp_confirmed_signal(
        self,
        wired_client: TestClient,
        fake_store: FakeJobStore,
        fake_temporal: FakeTemporalAdapter,
    ) -> None:
        """POST /jobs/{id}/otp-confirmed signals Temporal workflow."""
        job = _make_job(status=JobStatus.OTP_REQUIRED)
        fake_store.save_job(job)

        resp = wired_client.post(f"/jobs/{job.id}/otp-confirmed", headers=AUTH)
        assert resp.status_code == 204, resp.text
        assert job.id in fake_temporal.otp_signals

    def test_otp_confirmed_wrong_status(
        self,
        wired_client: TestClient,
        fake_store: FakeJobStore,
    ) -> None:
        """POST /jobs/{id}/otp-confirmed returns 409 when job not in otp_required."""
        job = _make_job(status=JobStatus.RUNNING)
        fake_store.save_job(job)

        resp = wired_client.post(f"/jobs/{job.id}/otp-confirmed", headers=AUTH)
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /jobs/{id}/cancel
# ---------------------------------------------------------------------------


class TestCancelJob:
    def test_cancel_signal(
        self,
        wired_client: TestClient,
        fake_store: FakeJobStore,
        fake_temporal: FakeTemporalAdapter,
    ) -> None:
        """POST /jobs/{id}/cancel signals Temporal to cancel workflow."""
        job = _make_job(status=JobStatus.RUNNING)
        fake_store.save_job(job)

        resp = wired_client.post(f"/jobs/{job.id}/cancel", headers=AUTH)
        assert resp.status_code == 204, resp.text
        assert job.id in fake_temporal.cancellations


# ---------------------------------------------------------------------------
# POST /maps/{bank}/proposals/{id}/approve and reject
# ---------------------------------------------------------------------------


class TestRemapProposals:
    def test_remap_approve_signal(
        self,
        wired_client: TestClient,
        fake_store: FakeJobStore,
        fake_temporal: FakeTemporalAdapter,
    ) -> None:
        """POST /maps/{bank}/proposals/{id}/approve signals workflow + marks storage."""
        proposal = _make_proposal()
        fake_store.save_proposal(proposal)

        resp = wired_client.post(
            f"/maps/banco_general/proposals/{proposal.id}/approve",
            headers=AUTH,
        )
        assert resp.status_code == 204, resp.text
        assert proposal.id in fake_temporal.remap_signals

    def test_remap_reject_no_signal(
        self,
        wired_client: TestClient,
        fake_store: FakeJobStore,
        fake_temporal: FakeTemporalAdapter,
    ) -> None:
        """POST /maps/{bank}/proposals/{id}/reject does NOT signal Temporal."""
        proposal = _make_proposal()
        fake_store.save_proposal(proposal)

        resp = wired_client.post(
            f"/maps/banco_general/proposals/{proposal.id}/reject",
            headers=AUTH,
        )
        assert resp.status_code == 204, resp.text
        assert proposal.id not in fake_temporal.remap_signals

    def test_remap_proposal_404(
        self,
        wired_client: TestClient,
    ) -> None:
        """Returns 404 for unknown proposal."""
        resp = wired_client.post(
            "/maps/banco_general/proposals/nonexistent/approve",
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_remap_proposal_expired(
        self,
        wired_client: TestClient,
        fake_store: FakeJobStore,
    ) -> None:
        """Returns 410 when proposal is expired."""
        proposal = _make_proposal(expires_future=False)
        fake_store.save_proposal(proposal)

        resp = wired_client.post(
            f"/maps/banco_general/proposals/{proposal.id}/approve",
            headers=AUTH,
        )
        assert resp.status_code == 410


# ---------------------------------------------------------------------------
# GET /banks
# ---------------------------------------------------------------------------


class TestGetBanks:
    def test_get_banks_returns_list(
        self,
        wired_client: TestClient,
    ) -> None:
        """GET /banks returns a non-empty list with banco_general."""
        resp = wired_client.get("/banks", headers=AUTH)
        assert resp.status_code == 200, resp.text
        banks = resp.json()
        assert isinstance(banks, list)
        bank_ids = [b["bank_id"] for b in banks]
        assert "banco_general" in bank_ids, (
            f"Expected 'banco_general' in banks list, got: {bank_ids}"
        )

    def test_get_banks_structure(
        self,
        wired_client: TestClient,
    ) -> None:
        """Each bank entry has required fields."""
        resp = wired_client.get("/banks", headers=AUTH)
        banks = resp.json()
        assert len(banks) > 0, "No banks found — check _BANKS_DIR path resolution"
        for bank in banks:
            assert "bank_id" in bank
            assert "display_name" in bank
            assert "country" in bank
            assert "map_version" in bank
            assert "circuit_status" in bank


# ---------------------------------------------------------------------------
# GET /accounts
# ---------------------------------------------------------------------------


class TestGetAccounts:
    def test_get_accounts_lists_from_storage(
        self,
        wired_client: TestClient,
        fake_store: FakeJobStore,
    ) -> None:
        """GET /accounts returns accounts from storage."""
        from datetime import date  # noqa: PLC0415

        from open_banca_domain.entities.account import SavingsAccount  # noqa: PLC0415

        acc = SavingsAccount(
            id=str(uuid4()),
            bank_account_id="001-111",
            account_type="savings",
            balance=Decimal("1000.00"),
            currency="USD",
            opened_at=date(2020, 1, 1),
        )
        fake_store.save_account(acc)

        resp = wired_client.get("/accounts", headers=AUTH)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) >= 1


# ---------------------------------------------------------------------------
# POST /webhooks/test
# ---------------------------------------------------------------------------


class TestWebhooksTest:
    def test_webhooks_test_no_url_configured(
        self,
        wired_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /webhooks/test returns 422 when OPEN_BANCA_WEBHOOK_URL is unset."""
        monkeypatch.delenv("OPEN_BANCA_WEBHOOK_URL", raising=False)
        resp = wired_client.post(
            "/webhooks/test",
            json={"event_type": "job.created"},
            headers=AUTH,
        )
        assert resp.status_code == 422

    def test_webhooks_test_dispatches(
        self,
        wired_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """POST /webhooks/test dispatches signed webhook via httpx."""
        monkeypatch.setenv("OPEN_BANCA_WEBHOOK_URL", "http://test-receiver.local/hooks")
        monkeypatch.setenv("OPEN_BANCA_WEBHOOK_SECRET", "test-secret")

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)

            resp = wired_client.post(
                "/webhooks/test",
                json={"event_type": "job.created"},
                headers=AUTH,
            )

        assert resp.status_code == 204, resp.text
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "X-OpenBanca-Signature" in call_kwargs.kwargs["headers"]


# ---------------------------------------------------------------------------
# No more 501 responses
# ---------------------------------------------------------------------------


_NO501_ENDPOINTS: list[tuple[str, str]] = [
    ("GET", "/jobs/some-id"),
    ("GET", "/jobs/some-id/result"),
    ("POST", "/jobs/some-id/otp-confirmed"),
    ("POST", "/jobs/some-id/cancel"),
    ("POST", "/maps/banco_general/proposals/prop-id/approve"),
    ("POST", "/maps/banco_general/proposals/prop-id/reject"),
    ("GET", "/banks"),
    ("GET", "/accounts"),
    ("POST", "/webhooks/test"),
]


class TestNo501:
    """Verify no endpoint returns 501 once wired."""

    @pytest.mark.parametrize("method,path", _NO501_ENDPOINTS)
    def test_no_more_501(
        self,
        wired_client: TestClient,
        method: str,
        path: str,
    ) -> None:
        """Wired endpoints must NOT return 501."""
        body: dict[str, str] | None = None
        if path == "/webhooks/test":
            body = {"event_type": "job.created"}

        if body:
            resp = wired_client.request(method, path, headers=AUTH, json=body)  # type: ignore[arg-type]
        else:
            resp = wired_client.request(method, path, headers=AUTH)
        assert resp.status_code != 501, (
            f"{method} {path} still returns 501 — endpoint is not wired."
        )


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


class TestDependencyInjection:
    def test_dependency_injection_resolvable(
        self,
        fake_store: FakeJobStore,
        fake_temporal: FakeTemporalAdapter,
        fake_event_bus: FakeEventBus,
    ) -> None:
        """All dependency factories are importable and resolvable (no circular imports)."""
        # Importing these verifies no circular import errors at module load time
        from open_banca_api.dependencies import (  # noqa: PLC0415
            get_apply_remap_uc,
            get_confirm_otp_uc,
            get_get_job_result_uc,
            get_list_accounts_uc,
            get_start_scrape_job_uc,
        )

        # All symbols imported successfully means no circular import
        assert all(
            callable(fn)
            for fn in [
                get_confirm_otp_uc,
                get_apply_remap_uc,
                get_get_job_result_uc,
                get_list_accounts_uc,
                get_start_scrape_job_uc,
            ]
        )
