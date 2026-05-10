"""Map proposal endpoints — approve/reject bank map proposals. All stubs."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from open_banca_api.auth import verify_bearer

router = APIRouter(
    prefix="/maps",
    tags=["maps"],
    dependencies=[Depends(verify_bearer)],
)

_NOT_IMPLEMENTED = "Not implemented — wired in task #31"


@router.post(
    "/{bank}/proposals/{proposal_id}/approve",
    summary="Approve a remap proposal",
    description="Approves a proposed bank map patch.",
)
async def approve_proposal(bank: str, proposal_id: str) -> None:
    """POST /maps/{bank}/proposals/{id}/approve stub."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=_NOT_IMPLEMENTED)


@router.post(
    "/{bank}/proposals/{proposal_id}/reject",
    summary="Reject a remap proposal",
    description="Rejects a proposed bank map patch.",
)
async def reject_proposal(bank: str, proposal_id: str) -> None:
    """POST /maps/{bank}/proposals/{id}/reject stub."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=_NOT_IMPLEMENTED)
