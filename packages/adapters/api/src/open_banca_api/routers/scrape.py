"""POST /scrape — create a new scrape job.

Incremental mode logic:
  1. If mode == "full" → since_cursor = None (workflow fetches 6-month history).
  2. If mode == "incremental" and request.since is set → use it directly.
  3. If mode == "incremental" and request.since is None:
       - Query DedupEngine.effective_since(account_id) per requested account.
       - If ALL accounts have no cursor (first run) → fallback to mode="full".
       - Otherwise use the EARLIEST effective_since across all accounts so
         that the date window covers every account uniformly.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from open_banca_api.auth import verify_bearer
from open_banca_api.dependencies import (
    TemporalOrchestratorAdapter,
    get_dedup_engine,
    get_job_store,
    get_temporal_client,
)
from open_banca_api.schemas.scrape import JobCursorResponse, ScrapeRequest, ScrapeResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="",
    tags=["scrape"],
    dependencies=[Depends(verify_bearer)],
)


def _cred_hash(credentials: str) -> str:
    """SHA-256 truncated hash of credentials for keying (not stored in logs)."""
    return hashlib.sha256(credentials.encode()).hexdigest()[:16]


def _body_hash(body: ScrapeRequest) -> str:
    """Canonical hash of the request body for idempotency comparison."""
    return hashlib.sha256(body.model_dump_json().encode()).hexdigest()


def _resolve_incremental_cursor(
    accounts: list[str],
    dedup_engine: object,
    request_since: datetime | None,
) -> tuple[str, str | None]:
    """Resolve the effective mode and since_cursor for an incremental request.

    Args:
        accounts: Account IDs to scrape.  May be empty (all accounts).
        dedup_engine: DedupEngine instance for cursor queries.
        request_since: Explicit ``since`` override from the request body.

    Returns:
        Tuple of (resolved_mode, since_cursor_iso) where:
        - resolved_mode: "full" or "incremental"
        - since_cursor_iso: ISO-8601 string or None

    Notes:
        - The buffer (cursor - 3 days) is already applied by
          ``DedupEngine.effective_since()``.  The workflow must NOT apply
          an additional subtraction.
        - When no account list is provided, cursor cannot be queried at the
          API layer — falls back to "full" mode with a warning.
    """
    if request_since is not None:
        return "incremental", request_since.isoformat()

    if not accounts:
        # No account filter → cannot query cursors at API layer
        logger.warning(
            "Incremental mode with no account filter: cannot query cursors at API layer. "
            "Falling back to full mode."
        )
        return "full", None

    # Query effective_since per account
    effective_datetimes: list[datetime] = []
    for account_id in accounts:
        es = dedup_engine.effective_since(account_id)  # type: ignore[attr-defined]
        if es is not None:
            effective_datetimes.append(es)

    if not effective_datetimes:
        # No cursors exist — first-time run, fall back to full
        logger.info(
            "Incremental mode: no prior cursor found for accounts=%s. "
            "Falling back to full mode.",
            accounts,
        )
        return "full", None

    # Use the EARLIEST effective_since so every account's window is covered
    earliest = min(effective_datetimes)
    return "incremental", earliest.isoformat()


@router.post(
    "/scrape",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ScrapeResponse,
    summary="Create a new scrape job",
    description=(
        "Enqueues a new scrape job. Requires `Idempotency-Key` header. "
        "Returns existing job if same Idempotency-Key + payload is replayed. "
        "When mode='incremental' and since is not set, the cursor is derived "
        "from DedupEngine.effective_since() (cursor - 3 days)."
    ),
)
async def create_scrape(
    request: Request,
    body: ScrapeRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    orchestrator: Annotated[TemporalOrchestratorAdapter, Depends(get_temporal_client)] = ...,  # type: ignore[assignment]
    job_store: Annotated[object, Depends(get_job_store)] = ...,  # type: ignore[assignment]
    dedup_engine: Annotated[object, Depends(get_dedup_engine)] = ...,  # type: ignore[assignment]
) -> ScrapeResponse:
    """POST /scrape — start a scrape workflow and persist the job."""
    now = datetime.now(UTC)
    request_hash = _body_hash(body)

    # ── Idempotency check ──────────────────────────────────────────────────
    if idempotency_key:
        existing = job_store.get_idempotency(idempotency_key)  # type: ignore[attr-defined]
        if existing is not None:
            if existing["request_hash"] != request_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "idempotency_key_conflict",
                        "message": (
                            "Idempotency-Key already used with a different request payload."
                        ),
                    },
                )
            # Same key + same payload → return existing job
            existing_job = job_store.load_job(existing["job_id"])  # type: ignore[attr-defined]
            if existing_job:
                return ScrapeResponse(
                    job_id=existing_job.id,
                    status=str(existing_job.status),
                    created_at=existing_job.created_at,
                )

    # ── Resolve mode and since_cursor ─────────────────────────────────────
    if body.mode == "full":
        resolved_mode = "full"
        since_cursor: str | None = None
    else:
        # Incremental — derive cursor from DedupEngine if not explicitly set
        resolved_mode, since_cursor = _resolve_incremental_cursor(
            accounts=body.accounts,
            dedup_engine=dedup_engine,
            request_since=body.since,
        )

    # ── Create new job ─────────────────────────────────────────────────────
    job_id = str(uuid4())

    # Import here to avoid circular imports at module level
    from open_banca_domain.entities.job import Job, JobMode, JobStatus

    job = Job(
        id=job_id,
        status=JobStatus.PENDING,
        bank=body.bank_id,
        credential_ref=body.credentials,
        mode=JobMode.FULL if resolved_mode == "full" else JobMode.INCREMENTAL,
        created_at=now,
        updated_at=now,
        since_cursor=since_cursor,
    )
    job_store.save_job(job)  # type: ignore[attr-defined]
    logger.info(
        "Job persisted: job_id=%s bank=%s mode=%s status=pending",
        job_id,
        body.bank_id,
        resolved_mode,
    )

    # ── Persist idempotency key ────────────────────────────────────────────
    if idempotency_key:
        job_store.save_idempotency(  # type: ignore[attr-defined]
            key=idempotency_key,
            request_hash=request_hash,
            job_id=job_id,
        )

    # ── Start Temporal workflow ────────────────────────────────────────────
    try:
        await orchestrator.async_start_workflow(
            job_id=job_id,
            bank_id=body.bank_id,
            credential_ref=body.credentials,
            mode=resolved_mode,
            since_cursor=since_cursor,
            account_filter=body.accounts or None,
        )
    except Exception as exc:
        logger.error("Failed to start workflow for job %s: %s", job_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "workflow_start_failed",
                "message": f"Failed to start scrape workflow: {exc}",
            },
        ) from exc

    return ScrapeResponse(job_id=job_id, status="pending", created_at=now)


@router.get(
    "/jobs/{job_id}/cursor",
    response_model=JobCursorResponse,
    summary="Get current per-account cursors",
    description=(
        "Returns DedupEngine.effective_since (cursor - 3 days) for each requested "
        "account_id.  Pass account IDs as repeated query params: "
        "?accounts=acc-001&accounts=acc-002.  The job must exist (404 otherwise). "
        "Useful for debugging incremental scrape windows."
    ),
)
async def get_job_cursor(
    job_id: str,
    accounts: Annotated[list[str] | None, Query(description="Account IDs to query cursors for")] = None,
    job_store: Annotated[object, Depends(get_job_store)] = ...,  # type: ignore[assignment]
    dedup_engine: Annotated[object, Depends(get_dedup_engine)] = ...,  # type: ignore[assignment]
) -> JobCursorResponse:
    """GET /jobs/{id}/cursor — return per-account effective_since cursors.

    Query params:
        accounts: Repeated list of account_id strings to query.  If omitted,
                  bank accounts are looked up via SqliteJobStore.list_accounts_by_bank
                  if available; otherwise returns an empty dict.
    """

    job = job_store.load_job(job_id)  # type: ignore[attr-defined]
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "job_not_found", "message": f"Job {job_id!r} not found."},
        )

    # Resolve account list: prefer explicit query params, fall back to bank accounts
    account_list: list[str] = list(accounts) if accounts else []

    if not account_list:
        # Try SqliteJobStore.list_accounts_by_bank if available
        try:
            account_objs = job_store.list_accounts_by_bank(job.bank)  # type: ignore[attr-defined]
            account_list = [a.id for a in account_objs]
        except (AttributeError, Exception):
            account_list = []

    cursors: dict[str, str] = {}
    for account_id in account_list:
        effective = dedup_engine.effective_since(account_id)  # type: ignore[attr-defined]
        if effective is not None:
            cursors[account_id] = effective.isoformat()

    return JobCursorResponse(job_id=job_id, cursors=cursors)
