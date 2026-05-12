"""Map proposal endpoints — approve/reject bank map proposals.

On approve (v1 HITL-only):
  1. Load + validate proposal (404/410/409).
  2. Parse patch_diff JSON → RemapPatch.
  3. Apply patch to packages/banks/{bank}/map.json via MapPatcher (atomic write).
  4. Validate rebuilt map (linter — BankMap schema, enforced pre-write).
  5. git commit + tag via git_apply helper.
  6. Mark proposal applied in storage.
  7. Signal remap_approved to Temporal workflow.
  8. Return 200 with diff metadata, commit SHA, and tag.

On reject:
  1. Load + validate proposal (404/410/409).
  2. Mark rejected in storage.
  3. Signal remap_rejected to Temporal workflow.
  4. Return 204.

Architectural note: docs say "apply corre dentro de RemapBankWorkflow".
In v1 (HITL-only) the API applies the patch directly (ADR-0013 amendment).
The workflow receives the signal after the fact.  Auto-apply inside the
workflow is deferred to v1.x.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from open_banca_api.auth import verify_bearer
from open_banca_api.dependencies import (
    TemporalOrchestratorAdapter,
    get_apply_remap_uc,
    get_job_store,
    get_temporal_client,
)
from open_banca_api.services.git_apply import GitApplyError, git_commit_and_tag
from open_banca_api.services.map_patcher import apply_patch_to_disk
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


def _get_banks_root() -> Path:
    """Resolve the packages/banks directory.

    Reads OPEN_BANCA_BANKS_ROOT env var; falls back to a path relative to this
    file's location (packages/adapters/api/src/.../routers/ → packages/banks/).
    """
    env_val = os.environ.get("OPEN_BANCA_BANKS_ROOT")
    if env_val:
        return Path(env_val)
    # __file__ = .../packages/adapters/api/src/open_banca_api/routers/maps.py
    # 6 levels up from __file__ lands at the monorepo root, then packages/banks
    monorepo_root = Path(__file__).resolve().parents[6]
    return monorepo_root / "packages" / "banks"


def _parse_patch_diff(patch_diff: str, proposal_id: str) -> dict[str, Any]:
    """Parse the stored patch_diff JSON string.  Raises HTTPException on invalid JSON."""
    try:
        return json.loads(patch_diff)  # type: ignore[no-any-return]
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_patch_diff", "message": str(exc), "proposal_id": proposal_id},
        ) from exc


@router.post(
    "/{bank}/proposals/{proposal_id}/approve",
    summary="Approve a remap proposal",
    description=(
        "Approves a proposed bank map patch, applies it to disk, git-commits + tags, "
        "and signals the waiting Temporal workflow."
    ),
)
async def approve_proposal(
    bank: str,
    proposal_id: str,
    body: ProposalActionRequest | None = None,
    uc: Annotated[ApplyRemapProposal, Depends(get_apply_remap_uc)] = ...,  # type: ignore[assignment]
    job_store: Annotated[object, Depends(get_job_store)] = ...,  # type: ignore[assignment]
    orchestrator: Annotated[TemporalOrchestratorAdapter, Depends(get_temporal_client)] = ...,  # type: ignore[assignment]
) -> dict[str, Any]:
    """POST /maps/{bank}/proposals/{id}/approve — full apply pipeline."""
    from datetime import UTC, datetime

    from open_banca_domain.entities.remap_proposal import RemapStatus

    # ── 1. Load proposal ──────────────────────────────────────────────────────
    proposal = job_store.load_proposal(proposal_id)  # type: ignore[attr-defined]
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "proposal_not_found", "proposal_id": proposal_id},
        )

    # ── 2. Check status (409 if already resolved) ────────────────────────────
    if proposal.status not in (RemapStatus.PENDING,):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "proposal_already_resolved",
                "status": str(proposal.status),
            },
        )

    # ── 3. Check TTL (410 if expired) ────────────────────────────────────────
    if proposal.expires_at < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"error": "proposal_expired", "expires_at": proposal.expires_at.isoformat()},
        )

    # ── 4. Parse patch_diff ───────────────────────────────────────────────────
    patch_data = _parse_patch_diff(proposal.patch_diff, proposal_id)
    target_step_index: int = patch_data.get("target_step_index", 0)
    new_steps: list[dict[str, Any]] = patch_data.get("new_steps", [])

    # ── 5. Locate bank directory ──────────────────────────────────────────────
    banks_root = _get_banks_root()
    bank_dir = banks_root / bank
    if not bank_dir.is_dir():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "bank_not_found", "bank": bank},
        )

    # ── 6. Apply patch to disk (validate pre-write via BankMap schema) ────────
    try:
        apply_result = apply_patch_to_disk(
            bank_dir=bank_dir,
            target_step_index=target_step_index,
            new_steps=new_steps,
        )
    except ValueError as exc:
        # Linter / schema validation failure — do NOT commit
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "linter_failed", "message": str(exc), "proposal_id": proposal_id},
        ) from exc
    except (FileNotFoundError, IndexError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "map_not_found", "message": str(exc)},
        ) from exc

    # ── 7. Git commit + tag ───────────────────────────────────────────────────
    repo_root = _get_banks_root().parent.parent  # packages/ → monorepo root
    map_rel_path = str(apply_result.map_path.relative_to(repo_root))

    try:
        git_result = git_commit_and_tag(
            repo_root=repo_root,
            bank=bank,
            proposal_id=proposal_id,
            new_version=apply_result.new_version,
            map_rel_path=map_rel_path,
        )
    except (GitApplyError, ValueError) as exc:
        # Roll back: restore from .bak
        if apply_result.backup_path.exists():
            shutil.copy2(apply_result.backup_path, apply_result.map_path)
            logger.warning("Rolled back map.json from .bak after git failure")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "git_apply_failed", "message": str(exc)},
        ) from exc

    # ── 8. Mark proposal applied in storage ───────────────────────────────────
    try:
        uc.execute(ApplyRemapInput(proposal_id=proposal_id, action=RemapAction.APPROVE))
    except ValueError as exc:
        logger.warning("apply_remap_uc.execute failed (non-fatal): %s", exc)

    # ── 9. Signal Temporal workflow ───────────────────────────────────────────
    try:
        await orchestrator.async_signal_remap_approved(proposal_id)
    except Exception as exc:
        logger.warning("Temporal signal failed for proposal %s: %s", proposal_id, exc)
        # Don't fail the request — patch is already applied and committed

    return {
        "proposal_id": proposal_id,
        "bank": bank,
        "old_version": apply_result.old_version,
        "new_version": apply_result.new_version,
        "commit_sha": git_result.commit_sha,
        "tag": git_result.tag,
        "steps_replaced": apply_result.steps_replaced,
        "new_steps_count": apply_result.new_steps_count,
    }


@router.post(
    "/{bank}/proposals/{proposal_id}/reject",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reject a remap proposal",
    description="Rejects a proposed bank map patch and signals the waiting workflow.",
)
async def reject_proposal(
    bank: str,
    proposal_id: str,
    body: ProposalActionRequest | None = None,
    uc: Annotated[ApplyRemapProposal, Depends(get_apply_remap_uc)] = ...,  # type: ignore[assignment]
    job_store: Annotated[object, Depends(get_job_store)] = ...,  # type: ignore[assignment]
    orchestrator: Annotated[TemporalOrchestratorAdapter, Depends(get_temporal_client)] = ...,  # type: ignore[assignment]
) -> None:
    """POST /maps/{bank}/proposals/{id}/reject."""
    from datetime import UTC, datetime

    from open_banca_domain.entities.remap_proposal import RemapStatus

    # ── 1. Load proposal ──────────────────────────────────────────────────────
    proposal = job_store.load_proposal(proposal_id)  # type: ignore[attr-defined]
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "proposal_not_found", "proposal_id": proposal_id},
        )

    # ── 2. Check status ───────────────────────────────────────────────────────
    if proposal.status not in (RemapStatus.PENDING,):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "proposal_already_resolved",
                "status": str(proposal.status),
            },
        )

    # ── 3. Check TTL ──────────────────────────────────────────────────────────
    if proposal.expires_at < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"error": "proposal_expired", "expires_at": proposal.expires_at.isoformat()},
        )

    # ── 4. Mark rejected in storage ───────────────────────────────────────────
    try:
        uc.execute(ApplyRemapInput(proposal_id=proposal_id, action=RemapAction.REJECT))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "apply_failed", "message": str(exc)},
        ) from exc

    # ── 5. Signal Temporal workflow ───────────────────────────────────────────
    try:
        await orchestrator.async_signal_remap_rejected(proposal_id)
    except Exception as exc:
        logger.warning("Temporal reject signal failed for proposal %s: %s", proposal_id, exc)
