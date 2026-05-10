"""Shared pytest fixtures for open-banca API tests."""

from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Set required env vars BEFORE importing the app so Settings picks them up
os.environ.setdefault("OPEN_BANCA_API_TOKEN", "test-token-abc123")
os.environ.setdefault("OPEN_BANCA_DB_PATH", ":memory:")

from open_banca_api.config import get_settings  # noqa: E402
from open_banca_api.dependencies import (  # noqa: E402
    get_job_store,
    get_temporal_client,
    get_webhook_outbox,
)
from open_banca_api.main import create_app  # noqa: E402


class _NullJobStore:
    """Minimal job store that returns None/empty for all operations."""

    def save_job(self, job: Any) -> None:
        pass

    def load_job(self, job_id: str) -> Any:
        return None

    def list_jobs(self) -> list[Any]:
        return []

    def save_account(self, account: Any) -> None:
        pass

    def save_transaction(self, tx: Any) -> None:
        pass

    def get_cursor(self, bank: str) -> Any:
        return None

    def save_cursor(self, bank: str, cursor: str) -> None:
        pass

    def list_accounts_by_bank(self, bank: str) -> list[Any]:
        return []

    def list_transactions_by_job(self, job_id: str) -> list[Any]:
        return []

    def save_proposal(self, proposal: Any) -> None:
        pass

    def load_proposal(self, proposal_id: str) -> Any:
        return None

    def get_idempotency(self, key: str) -> Any:
        return None

    def save_idempotency(self, key: str, request_hash: str, job_id: str) -> None:
        pass


class _NullTemporalAdapter:
    """Temporal adapter that raises 503 for any real operation (no Temporal server)."""

    async def async_start_workflow(self, **kwargs: Any) -> None:
        raise RuntimeError("No Temporal server available in this test fixture.")

    async def async_signal_otp_confirmed(self, job_id: str) -> None:
        raise RuntimeError("No Temporal server available in this test fixture.")

    async def async_signal_remap_approved(self, proposal_id: str) -> None:
        raise RuntimeError("No Temporal server available in this test fixture.")

    async def async_cancel_job(self, job_id: str) -> None:
        raise RuntimeError("No Temporal server available in this test fixture.")

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


@pytest.fixture(scope="session")
def app() -> object:
    """Create a fresh app instance with null adapters for the test session."""
    get_settings.cache_clear()
    _app = create_app()

    # Override dependencies so tests don't need a running Temporal or DB
    _app.dependency_overrides[get_job_store] = lambda: _NullJobStore()
    _app.dependency_overrides[get_temporal_client] = lambda: _NullTemporalAdapter()
    _app.dependency_overrides[get_webhook_outbox] = lambda: _NullEventBus()

    return _app


@pytest.fixture(scope="session")
def client(app: object) -> TestClient:  # type: ignore[type-arg]
    """TestClient for the full app."""
    return TestClient(app, raise_server_exceptions=True)  # type: ignore[arg-type]


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Valid authorization headers."""
    return {"Authorization": "Bearer test-token-abc123"}
