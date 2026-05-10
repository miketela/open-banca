"""POST /scrape — create a new scrape job.

Stub implementation: returns 501.
Rate-limited by slowapi (60/minute per bearer token).
Concurrency guard: max 1 concurrent job per (bank_id, credential hash).
"""
from __future__ import annotations

import hashlib
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from open_banca_api.auth import verify_bearer
from open_banca_api.schemas.scrape import ScrapeRequest

logger = logging.getLogger(__name__)

# In-memory concurrency registry: (bank_id, cred_hash) -> job_id
_active_scrapes: dict[tuple[str, str], str] = {}

router = APIRouter(
    prefix="",
    tags=["scrape"],
    dependencies=[Depends(verify_bearer)],
)


def _cred_hash(credentials: str) -> str:
    """SHA-256 truncated hash of credentials for keying (not stored in logs)."""
    return hashlib.sha256(credentials.encode()).hexdigest()[:16]


@router.post(
    "/scrape",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a new scrape job",
    description=(
        "Enqueues a new scrape job. Requires `Idempotency-Key` header. "
        "Returns 409 if a concurrent job is already running for the same "
        "(bank, credential) combination. Returns 501 until wired in task #31."
    ),
)
async def create_scrape(
    request: Request,
    body: ScrapeRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> None:
    """POST /scrape stub — enforces concurrency guard before raising 501."""
    key = (body.bank_id, _cred_hash(body.credentials))

    if key in _active_scrapes:
        existing_job_id = _active_scrapes[key]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "concurrent_job_running",
                "message": (
                    f"A scrape job is already running for bank '{body.bank_id}'. "
                    f"Cancel or await job '{existing_job_id}' first."
                ),
                "job_id": existing_job_id,
            },
        )

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not implemented — wired in task #31",
    )
