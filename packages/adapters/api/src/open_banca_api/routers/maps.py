"""Map proposal endpoints — approve/reject bank map proposals."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from open_banca_api.auth import verify_bearer
from open_banca_api.dependencies import (
    TemporalOrchestratorAdapter,
    get_apply_remap_uc,
    get_job_store,
    get_temporal_client,
)
from open_banca_application.use_cases.apply_remap_proposal import (
    ApplyRemapInput,
    ApplyRemapProposal,
    RemapAction,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/maps",
    tags=["maps"],
    dependencies=[Depends(verify_bearer)],
)


class ProposalActionRequest(BaseModel):
    """Optional body for approve/reject."""

    reviewer_id: str | None = None
    note: str | None = None


@router.post(
    "/{bank}/proposals/{proposal_id}/approve",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Approve a remap proposal",
    description="Approves a proposed bank map patch and signals the waiting workflow.",
)
async def approve_proposal(
    bank: str,
    proposal_id: str,
    body: ProposalActionRequest | None = None,
    uc: Annotated[ApplyRemapProposal, Depends(get_apply_remap_uc)] = ...,  # type: ignore[assignment]
    job_store: Annotated[object, Depends(get_job_store)] = ...,  # type: ignore[assignment]
    orchestrator: Annotated[TemporalOrchestratorAdapter, Depends(get_temporal_client)] = ...,  # type: ignore[assignment]
) -> None:
    """POST /maps/{bank}/proposals/{id}/approve."""
    # Check proposal exists
    proposal = job_store.load_proposal(proposal_id)  # type: ignore[attr-defined]
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "proposal_not_found", "proposal_id": proposal_id},
        )

    from open_banca_domain.entities.remap_proposal import RemapStatus

    if proposal.status not in (RemapStatus.PENDING,):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "proposal_already_resolved",
                "status": str(proposal.status),
            },
        )

    # Check expiry
    from datetime import UTC, datetime

    if proposal.expires_at < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"error": "proposal_expired", "expires_at": proposal.expires_at.isoformat()},
        )

    try:
        uc.execute(ApplyRemapInput(proposal_id=proposal_id, action=RemapAction.APPROVE))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "apply_failed", "message": str(exc)},
        ) from exc

    # Signal the workflow directly (async) since use case port is sync
    try:
        await orchestrator.async_signal_remap_approved(proposal_id)
    except Exception as exc:
        logger.warning("Temporal signal failed for proposal %s: %s", proposal_id, exc)
        # Don't fail the request — proposal is already marked approved in storage


@router.post(
    "/{bank}/proposals/{proposal_id}/reject",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reject a remap proposal",
    description="Rejects a proposed bank map patch.",
)
async def reject_proposal(
    bank: str,
    proposal_id: str,
    body: ProposalActionRequest | None = None,
    uc: Annotated[ApplyRemapProposal, Depends(get_apply_remap_uc)] = ...,  # type: ignore[assignment]
    job_store: Annotated[object, Depends(get_job_store)] = ...,  # type: ignore[assignment]
) -> None:
    """POST /maps/{bank}/proposals/{id}/reject."""
    proposal = job_store.load_proposal(proposal_id)  # type: ignore[attr-defined]
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "proposal_not_found", "proposal_id": proposal_id},
        )

    from open_banca_domain.entities.remap_proposal import RemapStatus

    if proposal.status not in (RemapStatus.PENDING,):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "proposal_already_resolved",
                "status": str(proposal.status),
            },
        )

    from datetime import UTC, datetime

    if proposal.expires_at < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"error": "proposal_expired", "expires_at": proposal.expires_at.isoformat()},
        )

    try:
        uc.execute(ApplyRemapInput(proposal_id=proposal_id, action=RemapAction.REJECT))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "apply_failed", "message": str(exc)},
        ) from exc
