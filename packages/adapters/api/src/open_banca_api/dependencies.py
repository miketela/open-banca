"""FastAPI dependency providers for open-banca API.

Each provider follows the FastAPI Depends pattern — instances are created
per-request, ensuring no shared mutable state across requests.

Temporal client is cached as a module-level singleton (connection is
expensive) but only initialised on first use, not at import time.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends

from open_banca_api.config import Settings, get_settings
from open_banca_application.use_cases.apply_remap_proposal import ApplyRemapProposal
from open_banca_application.use_cases.confirm_otp import ConfirmOTP
from open_banca_application.use_cases.get_job_result import GetJobResult
from open_banca_application.use_cases.list_accounts import ListAccounts
from open_banca_application.use_cases.start_scrape_job import StartScrapeJob
from open_banca_domain.entities.webhook_event import WebhookEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Temporal orchestrator adapter
# ---------------------------------------------------------------------------


class TemporalOrchestratorAdapter:
    """Synchronous façade around the Temporal async client.

    Wraps ``temporalio.client.Client`` to satisfy ``OrchestratorPort``.
    Async methods are called via ``asyncio.get_event_loop().run_until_complete``
    inside a running event loop context using ``asyncio.ensure_future`` /
    ``loop.run_until_complete``.

    NOTE: FastAPI endpoints run inside an async event loop, so we must NOT
    call ``run_until_complete`` from within a coroutine.  Instead, endpoints
    that need Temporal are themselves ``async def`` and use the async client
    directly.  This adapter is provided for use cases that expect a
    synchronous port, and is only used in synchronous contexts.
    """

    def __init__(self, client: object, task_queue: str) -> None:
        self._client = client  # temporalio.client.Client
        self._task_queue = task_queue

    # OrchestratorPort sync interface — raises if called from an async context
    # (endpoints should use get_temporal_client() directly instead).
    def start_job(self, bank: str, credential_ref: str, mode: str) -> str:
        """Start a ScrapeJobWorkflow — call only from sync context."""
        raise RuntimeError(
            "start_job() is synchronous; call _async_start_job() from an async endpoint."
        )

    def signal_otp_confirmed(self, job_id: str) -> None:
        """Signal otp_confirmed — call only from sync context."""
        raise RuntimeError("Use async endpoint directly to signal Temporal workflows.")

    def signal_remap_approved(self, proposal_id: str) -> None:
        """Signal remap_approved — call only from sync context."""
        raise RuntimeError("Use async endpoint directly to signal Temporal workflows.")

    def cancel_job(self, job_id: str) -> None:
        """Cancel job — call only from sync context."""
        raise RuntimeError("Use async endpoint directly to signal Temporal workflows.")

    def query_status(self, job_id: str) -> str:
        """Query status — call only from sync context."""
        raise RuntimeError("Use async endpoint directly to query Temporal workflows.")

    # ── Async helpers called by endpoints directly ───────────────────────────

    async def async_start_workflow(
        self,
        job_id: str,
        bank_id: str,
        credential_ref: str,
        mode: str,
        since_cursor: str | None,
        account_filter: list[str] | None,
    ) -> None:
        """Start ScrapeJobWorkflow on Temporal."""
        from open_banca_orchestrator.workflows.scrape_job import (
            ScrapeJobInput,
            ScrapeJobWorkflow,
            ScrapeMode,
        )

        temporal_mode = (
            ScrapeMode.full_historical if mode == "full" else ScrapeMode.incremental
        )
        input_ = ScrapeJobInput(
            job_id=job_id,
            bank_id=bank_id,
            credential_ref=credential_ref,
            mode=temporal_mode,
            since_cursor=since_cursor,
            account_filter=account_filter or None,
        )
        await self._client.start_workflow(  # type: ignore[attr-defined]
            ScrapeJobWorkflow.run,
            input_,
            id=job_id,
            task_queue=self._task_queue,
        )
        logger.info("Started ScrapeJobWorkflow: job_id=%s bank=%s", job_id, bank_id)

    async def async_signal_otp_confirmed(self, job_id: str) -> None:
        """Send otp_confirmed signal to a running workflow."""
        handle = self._client.get_workflow_handle(job_id)  # type: ignore[attr-defined]
        await handle.signal("otp_confirmed")
        logger.info("Sent otp_confirmed signal: job_id=%s", job_id)

    async def async_signal_remap_approved(self, proposal_id: str) -> None:
        """Send remap_approved signal to the workflow waiting on a proposal."""
        handle = self._client.get_workflow_handle(proposal_id)  # type: ignore[attr-defined]
        await handle.signal("remap_approved")
        logger.info("Sent remap_approved signal: proposal_id=%s", proposal_id)

    async def async_signal_remap_rejected(self, proposal_id: str) -> None:
        """Send remap_rejected signal to the workflow waiting on a proposal."""
        handle = self._client.get_workflow_handle(proposal_id)  # type: ignore[attr-defined]
        await handle.signal("remap_rejected", "operator_rejected")
        logger.info("Sent remap_rejected signal: proposal_id=%s", proposal_id)

    async def async_signal_human_input_provided(
        self,
        job_id: str,
        field_key: str,
        answer: str,
        persist: bool,
    ) -> None:
        """Send human_input_provided signal to a running workflow (ADR-0021)."""
        handle = self._client.get_workflow_handle(job_id)  # type: ignore[attr-defined]
        await handle.signal("human_input_provided", field_key, answer, persist)
        logger.info(
            "Sent human_input_provided signal: job_id=%s field_key=%s persist=%s",
            job_id,
            field_key,
            persist,
        )

    async def async_cancel_job(self, job_id: str) -> None:
        """Cancel a running workflow."""
        handle = self._client.get_workflow_handle(job_id)  # type: ignore[attr-defined]
        await handle.cancel()
        logger.info("Cancelled workflow: job_id=%s", job_id)

    async def async_query_status(self, job_id: str) -> str:
        """Query the current status from the workflow."""
        handle = self._client.get_workflow_handle(job_id)  # type: ignore[attr-defined]
        try:
            result = await handle.query("current_status")
            return str(result)
        except Exception:
            return "unknown"


# ---------------------------------------------------------------------------
# Storage-backed EventBus (webhook outbox)
# ---------------------------------------------------------------------------


class WebhookOutboxEventBus:
    """EventBusPort implementation backed by the webhook_outbox table."""

    def __init__(self, outbox: object, webhook_secret: str) -> None:
        self._outbox = outbox  # WebhookOutbox
        self._secret = webhook_secret

    def publish(self, event: WebhookEvent) -> None:
        """Enqueue a webhook event in the outbox."""
        import hmac

        payload_json = event.model_dump_json()
        sig = hmac.new(
            self._secret.encode(),
            payload_json.encode(),
            hashlib.sha256,
        ).hexdigest()
        self._outbox.enqueue(  # type: ignore[attr-defined]
            job_id=event.job_id or "system",
            event_type=str(event.event_type),
            payload=event.payload,
            signature=sig,
        )

    def retry_pending(self) -> None:
        """No-op — delivery is handled by a background worker."""


# ---------------------------------------------------------------------------
# Dependency provider functions
# ---------------------------------------------------------------------------

_temporal_client_cache: object | None = None


async def get_temporal_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TemporalOrchestratorAdapter:
    """Return a cached Temporal client adapter (connects on first call)."""
    global _temporal_client_cache

    if _temporal_client_cache is None:
        from open_banca_orchestrator.client import get_client
        from open_banca_orchestrator.config import OrchestratorSettings

        orch_settings = OrchestratorSettings()
        _temporal_client_cache = await get_client(orch_settings)
        logger.info("Temporal client connected: %s", orch_settings.temporal_address)

    return TemporalOrchestratorAdapter(
        client=_temporal_client_cache,
        task_queue=_get_task_queue(settings),
    )


def _get_task_queue(settings: Settings) -> str:
    """Resolve temporal task queue from env (falls back to orchestrator default)."""
    return os.environ.get("OPEN_BANCA_TEMPORAL_TASK_QUEUE", "open-banca-task-queue")


def get_storage_connection() -> object:
    """Return an open SQLCipher connection (per-request).

    In production, reads OPEN_BANCA_DB_PATH and OPEN_BANCA_MASTER_PASSPHRASE
    from environment.  In tests, override via FastAPI dependency_overrides.
    """
    from open_banca_storage.connection import (
        ConnectionPool,
        PassthroughKeyDerivation,
    )
    from open_banca_storage.migrations import migrate

    db_path = Path(os.environ.get("OPEN_BANCA_DB_PATH", "/data/open_banca.db"))
    passphrase = os.environ.get("OPEN_BANCA_MASTER_PASSPHRASE", "dev-insecure-passphrase")

    try:
        from open_banca_storage.kdf import Argon2idKeyDerivation
        kdf = Argon2idKeyDerivation()
    except Exception:
        kdf = PassthroughKeyDerivation()  # type: ignore[assignment]

    pool = ConnectionPool(db_path=db_path, passphrase=passphrase, key_derivation=kdf)
    conn = pool.get()
    migrate(conn)
    return conn


def get_job_store(
    conn: Annotated[object, Depends(get_storage_connection)],
) -> object:
    """Return a SqliteJobStore wired to the current connection."""
    from open_banca_storage.repositories.job_store import SqliteJobStore

    return SqliteJobStore(conn)


def get_secret_vault() -> object:
    """Return a SecretVault for the current request."""
    from open_banca_storage import SecretVault
    from open_banca_storage.connection import ConnectionPool, PassthroughKeyDerivation

    db_path = Path(os.environ.get("OPEN_BANCA_DB_PATH", "/data/open_banca.db"))
    passphrase = os.environ.get("OPEN_BANCA_MASTER_PASSPHRASE", "dev-insecure-passphrase")
    try:
        from open_banca_storage.kdf import Argon2idKeyDerivation

        kdf = Argon2idKeyDerivation()
    except Exception:
        kdf = PassthroughKeyDerivation()  # type: ignore[assignment]

    pool = ConnectionPool(db_path=db_path, passphrase=passphrase, key_derivation=kdf)
    return SecretVault(pool=pool, master_passphrase=passphrase)


def get_webhook_outbox(
    conn: Annotated[object, Depends(get_storage_connection)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> WebhookOutboxEventBus:
    """Return a WebhookOutboxEventBus wired to the current connection."""
    from open_banca_storage.repositories.job_store import WebhookOutbox

    outbox = WebhookOutbox(conn)
    webhook_secret = os.environ.get("OPEN_BANCA_WEBHOOK_SECRET", "dev-insecure-webhook-secret")
    return WebhookOutboxEventBus(outbox=outbox, webhook_secret=webhook_secret)


def get_start_scrape_job_uc(
    orchestrator: Annotated[TemporalOrchestratorAdapter, Depends(get_temporal_client)],
    job_store: Annotated[object, Depends(get_job_store)],
    event_bus: Annotated[WebhookOutboxEventBus, Depends(get_webhook_outbox)],
) -> StartScrapeJob:
    """Wire StartScrapeJob use case (orchestrator port unused — endpoint calls async directly)."""
    # StartScrapeJob expects OrchestratorPort; we pass a no-op fake because
    # the endpoint calls orchestrator.async_start_workflow() directly.
    # The use case is only used here to emit the job.created event.
    return StartScrapeJob(
        orchestrator=orchestrator,  # type: ignore[arg-type]
        job_store=job_store,  # type: ignore[arg-type]
        event_bus=event_bus,
    )


def get_confirm_otp_uc(
    orchestrator: Annotated[TemporalOrchestratorAdapter, Depends(get_temporal_client)],
) -> ConfirmOTP:
    """Wire ConfirmOTP use case."""
    return ConfirmOTP(orchestrator=orchestrator)  # type: ignore[arg-type]


def get_apply_remap_uc(
    orchestrator: Annotated[TemporalOrchestratorAdapter, Depends(get_temporal_client)],
    job_store: Annotated[object, Depends(get_job_store)],
    event_bus: Annotated[WebhookOutboxEventBus, Depends(get_webhook_outbox)],
) -> ApplyRemapProposal:
    """Wire ApplyRemapProposal use case."""
    return ApplyRemapProposal(
        job_store=job_store,  # type: ignore[arg-type]
        orchestrator=orchestrator,  # type: ignore[arg-type]
        event_bus=event_bus,
    )


def get_get_job_result_uc(
    job_store: Annotated[object, Depends(get_job_store)],
) -> GetJobResult:
    """Wire GetJobResult use case."""
    return GetJobResult(job_store=job_store)  # type: ignore[arg-type]


def get_list_accounts_uc(
    job_store: Annotated[object, Depends(get_job_store)],
) -> ListAccounts:
    """Wire ListAccounts use case."""
    return ListAccounts(job_store=job_store)  # type: ignore[arg-type]


def get_dedup_engine(
    conn: Annotated[object, Depends(get_storage_connection)],
) -> object:
    """Return a DedupEngine backed by the current connection.

    Used by the scrape router to query per-account cursors for incremental mode.
    """
    from open_banca_storage.dedup.engine import DedupEngine

    return DedupEngine(conn)
