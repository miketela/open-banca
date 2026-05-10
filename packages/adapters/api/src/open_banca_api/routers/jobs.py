"""Job management endpoints — all stubs until task #31."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from open_banca_api.auth import verify_bearer

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    dependencies=[Depends(verify_bearer)],
)

_NOT_IMPLEMENTED = "Not implemented — wired in task #31"


@router.get(
    "/{job_id}",
    summary="Get job status and metadata",
    description="Returns current state and metadata for a job.",
)
async def get_job(job_id: str) -> None:
    """GET /jobs/{id} stub."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=_NOT_IMPLEMENTED)


@router.get(
    "/{job_id}/result",
    summary="Get job normalized result",
    description="Returns normalized accounts and transactions once job is completed.",
)
async def get_job_result(job_id: str) -> None:
    """GET /jobs/{id}/result stub."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=_NOT_IMPLEMENTED)


@router.post(
    "/{job_id}/otp-confirmed",
    summary="Confirm OTP (Clave Móvil push accepted)",
    description="Signals that the client has accepted the Clave Móvil push OTP.",
)
async def otp_confirmed(job_id: str) -> None:
    """POST /jobs/{id}/otp-confirmed stub."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=_NOT_IMPLEMENTED)


@router.post(
    "/{job_id}/cancel",
    summary="Cancel a job",
    description="Cancels a job in any phase.",
)
async def cancel_job(job_id: str) -> None:
    """POST /jobs/{id}/cancel stub."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=_NOT_IMPLEMENTED)
