"""Tests for task-32: incremental scrape mode — API params + cursor resolution.

Test matrix:
  - test_scrape_request_mode_default: default mode = "incremental".
  - test_scrape_full_mode: mode="full" → since ignored, full history.
  - test_scrape_incremental_first_time: no prior cursor → fallback to full + warning.
  - test_scrape_incremental_with_cursor: cursor exists → effective_since = cursor - 3d.
  - test_scrape_legacy_full_bool: full=True maps to mode="full".
  - test_scrape_mode_field_explicit: mode="incremental" explicit → incremental.
  - test_get_jobs_cursor_endpoint: returns current cursor map.
  - test_cursor_endpoint_job_not_found: 404 for unknown job_id.
  - test_resolve_incremental_cursor_no_accounts: no account filter → fallback full.
  - test_resolve_incremental_cursor_with_since: explicit since → pass through.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

# ── Ensure env vars are set before importing app ──────────────────────────────
os.environ.setdefault("OPEN_BANCA_API_TOKEN", "test-token-abc123")
os.environ.setdefault("OPEN_BANCA_DB_PATH", ":memory:")

from fastapi.testclient import TestClient  # noqa: E402

from open_banca_api.dependencies import (  # noqa: E402
    get_dedup_engine,
    get_job_store,
    get_temporal_client,
    get_webhook_outbox,
)
from open_banca_api.main import create_app  # noqa: E402
from open_banca_api.routers.scrape import _resolve_incremental_cursor  # noqa: E402
from open_banca_api.schemas.scrape import ScrapeRequest  # noqa: E402

# ── Helpers ───────────────────────────────────────────────────────────────────


class _NullJobStore:
    """Job store that stores jobs in memory for test assertions."""

    def __init__(self) -> None:
        self._jobs: dict[str, Any] = {}
        self._idem: dict[str, Any] = {}

    def save_job(self, job: Any) -> None:
        self._jobs[job.id] = job

    def load_job(self, job_id: str) -> Any:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[Any]:
        return list(self._jobs.values())

    def get_idempotency(self, key: str) -> Any:
        return self._idem.get(key)

    def save_idempotency(self, key: str, request_hash: str, job_id: str) -> None:
        self._idem[key] = {"request_hash": request_hash, "job_id": job_id}

    def list_accounts_for_job(self, job_id: str) -> list[str]:
        return []


class _NullTemporalAdapter:
    """Temporal adapter that succeeds silently."""

    async def async_start_workflow(self, **kwargs: Any) -> None:
        pass  # success — no exception

    async def async_signal_otp_confirmed(self, job_id: str) -> None:
        pass

    async def async_signal_remap_approved(self, proposal_id: str) -> None:
        pass

    async def async_cancel_job(self, job_id: str) -> None:
        pass

    async def async_query_status(self, job_id: str) -> str:
        return "unknown"

    def start_job(self, bank: str, credential_ref: str, mode: str) -> str:
        return "fake-job-id"

    def signal_otp_confirmed(self, job_id: str) -> None:
        pass

    def signal_remap_approved(self, proposal_id: str) -> None:
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


class _NullDedupEngine:
    """DedupEngine stub — no cursors (first-time run)."""

    def get_cursor(self, account_id: str) -> datetime | None:
        return None

    def effective_since(self, account_id: str) -> datetime | None:
        return None


class _CursorDedupEngine:
    """DedupEngine stub — returns a fixed cursor for acc-001."""

    def __init__(self, cursor: datetime) -> None:
        self._cursor = cursor

    def get_cursor(self, account_id: str) -> datetime | None:
        return self._cursor

    def effective_since(self, account_id: str) -> datetime | None:
        return self._cursor - timedelta(days=3)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def job_store() -> _NullJobStore:
    return _NullJobStore()


@pytest.fixture
def null_dedup() -> _NullDedupEngine:
    return _NullDedupEngine()


@pytest.fixture
def cursor_dedup() -> _CursorDedupEngine:
    fixed_cursor = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
    return _CursorDedupEngine(fixed_cursor)


def _make_client(
    job_store_inst: Any,
    dedup_engine_inst: Any,
) -> TestClient:
    from open_banca_api.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_job_store] = lambda: job_store_inst
    app.dependency_overrides[get_temporal_client] = lambda: _NullTemporalAdapter()
    app.dependency_overrides[get_webhook_outbox] = lambda: _NullEventBus()
    app.dependency_overrides[get_dedup_engine] = lambda: dedup_engine_inst
    return TestClient(app, raise_server_exceptions=True)


AUTH = {"Authorization": "Bearer test-token-abc123"}

# ── Schema tests (no HTTP) ─────────────────────────────────────────────────────


def test_scrape_request_mode_default() -> None:
    """Default mode is 'incremental'."""
    req = ScrapeRequest(bank_id="banco_general", credentials="enc")
    assert req.mode == "incremental"


def test_scrape_request_mode_full_explicit() -> None:
    """Explicit mode='full' is accepted."""
    req = ScrapeRequest(bank_id="banco_general", credentials="enc", mode="full")
    assert req.mode == "full"


def test_scrape_legacy_full_bool_promotes_to_mode_full() -> None:
    """Legacy full=True promotes mode to 'full'."""
    req = ScrapeRequest(bank_id="banco_general", credentials="enc", full=True)
    assert req.mode == "full"


def test_scrape_legacy_full_false_keeps_incremental() -> None:
    """full=False does not change the default mode='incremental'."""
    req = ScrapeRequest(bank_id="banco_general", credentials="enc", full=False)
    assert req.mode == "incremental"


# ── _resolve_incremental_cursor unit tests ────────────────────────────────────


def test_resolve_incremental_cursor_explicit_since() -> None:
    """Explicit since → pass through as-is, no cursor query needed."""
    since = datetime(2025, 5, 1, 0, 0, 0, tzinfo=UTC)
    engine = _NullDedupEngine()
    mode, cursor = _resolve_incremental_cursor(["acc-001"], engine, since)
    assert mode == "incremental"
    assert cursor == since.isoformat()


def test_resolve_incremental_cursor_no_accounts_fallback() -> None:
    """No account filter → cannot query cursors → fallback to full."""
    engine = _NullDedupEngine()
    mode, cursor = _resolve_incremental_cursor([], engine, None)
    assert mode == "full"
    assert cursor is None


def test_resolve_incremental_cursor_first_time_no_cursor() -> None:
    """No prior cursor → fallback to full."""
    engine = _NullDedupEngine()
    mode, cursor = _resolve_incremental_cursor(["acc-001"], engine, None)
    assert mode == "full"
    assert cursor is None


def test_resolve_incremental_cursor_with_cursor() -> None:
    """Prior cursor exists → effective_since = cursor - 3d returned."""
    fixed_cursor = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
    engine = _CursorDedupEngine(fixed_cursor)
    mode, cursor = _resolve_incremental_cursor(["acc-001"], engine, None)
    assert mode == "incremental"
    expected_since = fixed_cursor - timedelta(days=3)
    assert cursor == expected_since.isoformat()


def test_resolve_incremental_cursor_multiple_accounts_uses_earliest() -> None:
    """Multiple accounts → earliest effective_since is used."""

    class _MultiCursorEngine:
        def get_cursor(self, account_id: str) -> datetime | None:
            return {
                "acc-001": datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC),
                "acc-002": datetime(2025, 6, 10, 12, 0, 0, tzinfo=UTC),  # earlier
            }.get(account_id)

        def effective_since(self, account_id: str) -> datetime | None:
            c = self.get_cursor(account_id)
            if c is None:
                return None
            return c - timedelta(days=3)

    engine = _MultiCursorEngine()
    mode, cursor = _resolve_incremental_cursor(["acc-001", "acc-002"], engine, None)
    assert mode == "incremental"
    # Should be acc-002's effective_since: 2025-06-10 - 3d = 2025-06-07
    expected = (datetime(2025, 6, 10, 12, 0, 0, tzinfo=UTC) - timedelta(days=3)).isoformat()
    assert cursor == expected


def test_resolve_incremental_cursor_partial_missing() -> None:
    """Some accounts have no cursor → only use available cursors."""

    class _PartialEngine:
        def get_cursor(self, account_id: str) -> datetime | None:
            return datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC) if account_id == "acc-001" else None

        def effective_since(self, account_id: str) -> datetime | None:
            c = self.get_cursor(account_id)
            if c is None:
                return None
            return c - timedelta(days=3)

    engine = _PartialEngine()
    mode, cursor = _resolve_incremental_cursor(["acc-001", "acc-002"], engine, None)
    # acc-001 has a cursor; acc-002 does not → use acc-001's
    assert mode == "incremental"
    assert cursor is not None


# ── HTTP endpoint tests ────────────────────────────────────────────────────────


def test_scrape_full_mode_api(job_store: _NullJobStore, null_dedup: _NullDedupEngine) -> None:
    """POST /scrape with mode='full' → job created with mode=full, since_cursor=None."""
    client = _make_client(job_store, null_dedup)
    payload = {
        "bank_id": "banco_general",
        "credentials": "enc-blob",
        "mode": "full",
        "accounts": ["acc-001"],
    }
    resp = client.post("/scrape", json=payload, headers=AUTH)
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["status"] == "pending"

    # The stored job should have mode=FULL and no since_cursor
    job = job_store.load_job(data["job_id"])
    assert job is not None
    from open_banca_domain.entities.job import JobMode
    assert job.mode == JobMode.FULL
    assert job.since_cursor is None


def test_scrape_incremental_first_time_fallback(
    job_store: _NullJobStore, null_dedup: _NullDedupEngine
) -> None:
    """POST /scrape with mode='incremental', no prior cursor → fallback to full."""
    client = _make_client(job_store, null_dedup)
    payload = {
        "bank_id": "banco_general",
        "credentials": "enc-blob",
        "mode": "incremental",
        "accounts": ["acc-001"],
    }
    resp = client.post("/scrape", json=payload, headers=AUTH)
    assert resp.status_code == 202, resp.text

    job = job_store.load_job(resp.json()["job_id"])
    assert job is not None
    # First run → fallback to full
    from open_banca_domain.entities.job import JobMode
    assert job.mode == JobMode.FULL
    assert job.since_cursor is None


def test_scrape_incremental_with_cursor(
    job_store: _NullJobStore, cursor_dedup: _CursorDedupEngine
) -> None:
    """POST /scrape with mode='incremental', cursor exists → since = cursor - 3d."""
    client = _make_client(job_store, cursor_dedup)
    payload = {
        "bank_id": "banco_general",
        "credentials": "enc-blob",
        "mode": "incremental",
        "accounts": ["acc-001"],
    }
    resp = client.post("/scrape", json=payload, headers=AUTH)
    assert resp.status_code == 202, resp.text

    job = job_store.load_job(resp.json()["job_id"])
    assert job is not None
    from open_banca_domain.entities.job import JobMode
    assert job.mode == JobMode.INCREMENTAL
    assert job.since_cursor is not None
    # Cursor is 2025-06-15; effective_since = 2025-06-12
    expected_since = (datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC) - timedelta(days=3)).isoformat()
    assert job.since_cursor == expected_since


def test_scrape_incremental_explicit_since_override(
    job_store: _NullJobStore, null_dedup: _NullDedupEngine
) -> None:
    """Explicit since overrides cursor resolution even when no cursor exists."""
    client = _make_client(job_store, null_dedup)
    explicit_since = "2025-03-01T00:00:00+00:00"
    payload = {
        "bank_id": "banco_general",
        "credentials": "enc-blob",
        "mode": "incremental",
        "accounts": ["acc-001"],
        "since": explicit_since,
    }
    resp = client.post("/scrape", json=payload, headers=AUTH)
    assert resp.status_code == 202, resp.text

    job = job_store.load_job(resp.json()["job_id"])
    assert job is not None
    from open_banca_domain.entities.job import JobMode
    assert job.mode == JobMode.INCREMENTAL
    assert job.since_cursor is not None


def test_get_jobs_cursor_endpoint(
    job_store: _NullJobStore, null_dedup: _NullDedupEngine
) -> None:
    """GET /jobs/{id}/cursor returns a JobCursorResponse with job_id."""
    # First create a job
    client = _make_client(job_store, null_dedup)
    create_resp = client.post(
        "/scrape",
        json={"bank_id": "banco_general", "credentials": "enc", "accounts": ["acc-001"]},
        headers=AUTH,
    )
    assert create_resp.status_code == 202
    job_id = create_resp.json()["job_id"]

    # Query cursor endpoint
    resp = client.get(f"/jobs/{job_id}/cursor", headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["job_id"] == job_id
    assert "cursors" in data
    assert isinstance(data["cursors"], dict)


def test_cursor_endpoint_job_not_found(
    job_store: _NullJobStore, null_dedup: _NullDedupEngine
) -> None:
    """GET /jobs/{id}/cursor returns 404 for unknown job_id."""
    client = _make_client(job_store, null_dedup)
    resp = client.get("/jobs/unknown-job-xyz/cursor", headers=AUTH)
    assert resp.status_code == 404


def test_get_jobs_cursor_endpoint_with_cursors(
    job_store: _NullJobStore, cursor_dedup: _CursorDedupEngine
) -> None:
    """GET /jobs/{id}/cursor with ?accounts=acc-001 returns effective_since for that account."""
    client = _make_client(job_store, cursor_dedup)
    create_resp = client.post(
        "/scrape",
        json={"bank_id": "banco_general", "credentials": "enc", "accounts": ["acc-001"]},
        headers=AUTH,
    )
    assert create_resp.status_code == 202
    job_id = create_resp.json()["job_id"]

    # Pass account explicitly via query param
    resp = client.get(f"/jobs/{job_id}/cursor?accounts=acc-001", headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["job_id"] == job_id
    # cursor_dedup returns cursor - 3d for any account
    assert "acc-001" in data["cursors"]
    expected_since = (datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC) - timedelta(days=3)).isoformat()
    assert data["cursors"]["acc-001"] == expected_since


def test_scrape_default_mode_is_incremental_api(
    job_store: _NullJobStore, null_dedup: _NullDedupEngine
) -> None:
    """POST /scrape without mode field defaults to incremental."""
    client = _make_client(job_store, null_dedup)
    payload = {
        "bank_id": "banco_general",
        "credentials": "enc-blob",
        # No mode field → default is incremental
    }
    resp = client.post("/scrape", json=payload, headers=AUTH)
    # With no accounts and incremental → no cursor → fallback to full (still 202)
    assert resp.status_code == 202
    # Without accounts list the resolver falls back to full
    job = job_store.load_job(resp.json()["job_id"])
    from open_banca_domain.entities.job import JobMode
    # no accounts → no cursor query → full fallback
    assert job.mode == JobMode.FULL
