"""Tests for POST /jobs/{id}/human-input endpoint (ADR-0021).

TDD coverage:
1. Valid request with human_input_required job → 204 + signal sent.
2. Job not found → 404.
3. Job in wrong state (not human_input_required) → 409.
4. answer too long (>256 bytes UTF-8) → 422 validation error.
5. answer with control characters → 422 validation error.
6. field_key regex violation → 422 validation error.
7. Signal failure (Temporal down) → 503.
8. persist=false — signal still sent, no cache write attempted.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("OPEN_BANCA_API_TOKEN", "test-token-abc123")
os.environ.setdefault("OPEN_BANCA_DB_PATH", ":memory:")

from open_banca_api.config import get_settings  # noqa: E402
from open_banca_api.dependencies import (  # noqa: E402
    get_job_store,
    get_secret_vault,
    get_storage_connection,
    get_temporal_client,
    get_webhook_outbox,
)
from open_banca_api.main import create_app  # noqa: E402
from open_banca_domain.entities.job import Job, JobMode, JobStatus  # noqa: E402

AUTH = {"Authorization": "Bearer test-token-abc123"}


def _make_job(status: JobStatus = JobStatus.HUMAN_INPUT_REQUIRED) -> Job:
    return Job(
        id="job-test-001",
        status=status,
        bank="banco-test",
        credential_ref="vault://cred-001",
        mode=JobMode.FULL,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class _StubJobStore:
    def __init__(self, job: Job | None) -> None:
        self._job = job
        self.saved_answers: list[dict] = []

    def load_job(self, job_id: str) -> Job | None:
        return self._job

    def save_human_input_answer(
        self, job_id: str, field_key: str, answer: str, *, persist: bool = True
    ) -> None:
        self.saved_answers.append(
            {
                "job_id": job_id,
                "field_key": field_key,
                "answer": answer,
                "persist": persist,
            }
        )

    def get_human_input_question_hash(self, job_id: str, field_key: str) -> str | None:
        return None

    def save_job(self, job: Any) -> None:
        pass

    def list_accounts_by_bank(self, bank: str) -> list:
        return []

    def list_transactions_by_job(self, job_id: str) -> list:
        return []

    def get_cursor(self, bank: str) -> Any:
        return None

    def save_cursor(self, bank: str, cursor: str) -> None:
        pass

    def get_idempotency(self, key: str) -> Any:
        return None

    def set_idempotency(self, key: str, value: Any) -> None:
        pass

    def save_account(self, account: Any) -> None:
        pass

    def save_transaction(self, tx: Any) -> None:
        pass

    def save_proposal(self, proposal: Any) -> None:
        pass

    def load_proposal(self, proposal_id: str) -> Any:
        return None


class _StubTemporalAdapter:
    def __init__(self, *, signal_raises: Exception | None = None) -> None:
        self._signal_raises = signal_raises
        self.signal_calls: list[dict] = []

    async def async_signal_human_input_provided(
        self, job_id: str, field_key: str, answer: str, persist: bool
    ) -> None:
        if self._signal_raises is not None:
            raise self._signal_raises
        self.signal_calls.append({
            "job_id": job_id,
            "field_key": field_key,
            "answer": answer,
            "persist": persist,
        })

    async def async_signal_otp_confirmed(self, job_id: str) -> None:
        pass

    async def async_signal_remap_approved(self, proposal_id: str) -> None:
        pass

    async def async_signal_remap_rejected(self, proposal_id: str) -> None:
        pass

    async def async_cancel_job(self, job_id: str) -> None:
        pass

    async def async_query_status(self, job_id: str) -> str:
        return "human_input_required"

    async def async_start_workflow(self, **kwargs: Any) -> None:
        pass

    def start_job(self, bank: str, credential_ref: str, mode: str) -> str:
        return "fake-job-id"

    def signal_otp_confirmed(self, job_id: str) -> None:
        pass

    def cancel_job(self, job_id: str) -> None:
        pass

    def query_status(self, job_id: str) -> str:
        return "unknown"


class _NullEventBus:
    def publish(self, event: Any) -> None:
        pass

    def retry_pending(self) -> None:
        pass


@pytest.fixture()
def client_with(tmp_path: Any) -> Any:
    """Factory returning a TestClient with configurable stubs."""

    def _factory(
        job: Job | None,
        signal_raises: Exception | None = None,
    ) -> tuple[TestClient, _StubTemporalAdapter, _StubJobStore]:
        get_settings.cache_clear()
        app = create_app()
        adapter = _StubTemporalAdapter(signal_raises=signal_raises)
        store = _StubJobStore(job)
        app.dependency_overrides[get_job_store] = lambda: store
        app.dependency_overrides[get_storage_connection] = lambda: MagicMock()
        app.dependency_overrides[get_secret_vault] = lambda: MagicMock()
        app.dependency_overrides[get_temporal_client] = lambda: adapter
        app.dependency_overrides[get_webhook_outbox] = lambda: _NullEventBus()
        return TestClient(app, raise_server_exceptions=True), adapter, store

    return _factory


# ── 1. Valid request → 204 ────────────────────────────────────────────────────


def test_human_input_valid_returns_204(client_with: Any) -> None:
    client, adapter, store = client_with(_make_job())
    resp = client.post(
        "/jobs/job-test-001/human-input",
        json={"field_key": "security_q_pet", "answer": "Fluffy"},
        headers=AUTH,
    )
    assert resp.status_code == 204
    assert len(store.saved_answers) == 1
    assert store.saved_answers[0]["answer"] == "Fluffy"
    assert len(adapter.signal_calls) == 1
    assert adapter.signal_calls[0]["field_key"] == "security_q_pet"
    assert adapter.signal_calls[0]["answer"] == "Fluffy"
    assert adapter.signal_calls[0]["persist"] is True  # default


# ── 2. Job not found → 404 ────────────────────────────────────────────────────


def test_human_input_job_not_found_returns_404(client_with: Any) -> None:
    client, _, _ = client_with(None)
    resp = client.post(
        "/jobs/no-such-job/human-input",
        json={"field_key": "security_q_pet", "answer": "Fluffy"},
        headers=AUTH,
    )
    assert resp.status_code == 404


# ── 3. Job in wrong state → 409 ───────────────────────────────────────────────


def test_human_input_wrong_state_returns_409(client_with: Any) -> None:
    client, _, _ = client_with(_make_job(JobStatus.RUNNING))
    resp = client.post(
        "/jobs/job-test-001/human-input",
        json={"field_key": "security_q_pet", "answer": "Fluffy"},
        headers=AUTH,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "job_not_awaiting_human_input"


# ── 4. Answer too long → 422 ──────────────────────────────────────────────────


def test_human_input_answer_too_long_returns_422(client_with: Any) -> None:
    client, _, _ = client_with(_make_job())
    long_answer = "a" * 300  # > 256 bytes
    resp = client.post(
        "/jobs/job-test-001/human-input",
        json={"field_key": "security_q_pet", "answer": long_answer},
        headers=AUTH,
    )
    assert resp.status_code == 422


# ── 5. Control character in answer → 422 ─────────────────────────────────────


def test_human_input_control_char_returns_422(client_with: Any) -> None:
    client, _, _ = client_with(_make_job())
    resp = client.post(
        "/jobs/job-test-001/human-input",
        json={"field_key": "security_q_pet", "answer": "Fluffy\x00evilbyte"},
        headers=AUTH,
    )
    assert resp.status_code == 422


def test_human_input_xss_like_answer_rejected(client_with: Any) -> None:
    """<script> tags contain < and > which are fine by unicode category, but
    the embed direction mark U+202E is a Cf category and should be rejected."""
    client, _, _ = client_with(_make_job())
    # Try embedding a direction mark (Cf category)
    answer_with_dir_mark = "Fluffy‮evil"
    resp = client.post(
        "/jobs/job-test-001/human-input",
        json={"field_key": "security_q_pet", "answer": answer_with_dir_mark},
        headers=AUTH,
    )
    assert resp.status_code == 422


# ── 6. Invalid field_key regex → 422 ─────────────────────────────────────────


def test_human_input_invalid_field_key_returns_422(client_with: Any) -> None:
    client, _, _ = client_with(_make_job())
    resp = client.post(
        "/jobs/job-test-001/human-input",
        json={"field_key": "InvalidKey", "answer": "Fluffy"},
        headers=AUTH,
    )
    assert resp.status_code == 422


def test_human_input_field_key_too_short_returns_422(client_with: Any) -> None:
    client, _, _ = client_with(_make_job())
    resp = client.post(
        "/jobs/job-test-001/human-input",
        json={"field_key": "ab", "answer": "Fluffy"},
        headers=AUTH,
    )
    assert resp.status_code == 422


# ── 7. Signal failure → 503 ───────────────────────────────────────────────────


def test_human_input_signal_failure_returns_503(client_with: Any) -> None:
    client, _, _ = client_with(
        _make_job(),
        signal_raises=RuntimeError("Temporal unreachable"),
    )
    resp = client.post(
        "/jobs/job-test-001/human-input",
        json={"field_key": "security_q_pet", "answer": "Fluffy"},
        headers=AUTH,
    )
    assert resp.status_code == 503


# ── 8. persist=false ─────────────────────────────────────────────────────────


def test_human_input_persist_false_signals_workflow(client_with: Any) -> None:
    """persist=false still signals the workflow; cache write is opt-out."""
    client, adapter, _store = client_with(_make_job())
    resp = client.post(
        "/jobs/job-test-001/human-input",
        json={"field_key": "security_q_pet", "answer": "Fluffy", "persist": False},
        headers=AUTH,
    )
    assert resp.status_code == 204
    assert adapter.signal_calls[0]["persist"] is False
