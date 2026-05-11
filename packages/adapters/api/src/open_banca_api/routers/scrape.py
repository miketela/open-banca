"""POST /scrape — create a new scrape job."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from open_banca_api.auth import verify_bearer
from open_banca_api.dependencies import (
    TemporalOrchestratorAdapter,
    get_job_store,
    get_temporal_client,
)
from open_banca_api.schemas.scrape import ScrapeRequest, ScrapeResponse

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


@router.post(
    "/scrape",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ScrapeResponse,
    summary="Create a new scrape job",
    description=(
        "Enqueues a new scrape job. Requires `Idempotency-Key` header. "
        "Returns existing job if same Idempotency-Key + payload is replayed."
    ),
)
async def create_scrape(
    request: Request,
    body: ScrapeRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    orchestrator: Annotated[TemporalOrchestratorAdapter, Depends(get_temporal_client)] = ...,  # type: ignore[assignment]
    job_store: Annotated[object, Depends(get_job_store)] = ...,  # type: ignore[assignment]
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

    # ── Create new job ─────────────────────────────────────────────────────
    job_id = str(uuid4())
    mode = "full" if body.full else "incremental"
    since_cursor = body.since.isoformat() if body.since else None

    # Import here to avoid circular imports at module level
    from open_banca_domain.entities.job import Job, JobMode, JobStatus

    job = Job(
        id=job_id,
        status=JobStatus.PENDING,
        bank=body.bank_id,
        credential_ref=body.credentials,
        mode=JobMode.FULL if body.full else JobMode.INCREMENTAL,
        created_at=now,
        updated_at=now,
        since_cursor=since_cursor,
    )
    job_store.save_job(job)  # type: ignore[attr-defined]
    logger.info("Job persisted: job_id=%s bank=%s status=pending", job_id, body.bank_id)

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
            mode=mode,
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
