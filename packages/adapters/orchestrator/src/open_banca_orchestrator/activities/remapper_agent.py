"""RemapperAgentActivity — run the Remapper AI agent to fix a broken bank map.json.

Implements task 20 (RemapBankWorkflow + RemapperAgent).

Retry policy (orchestrator.md §Inventario):
  - NO retry (expensive LLM call — _RETRY_NONE).
  - start-to-close timeout: 15 min.
  - Heartbeat every 30 s.

Idempotency key: bank_id + breakage_hash + run_id.

v1 HITL-only (ADR-0013 amendment):
  - patch is NEVER auto-applied regardless of confidence or risk.
  - After dry-run passes, a RemapProposal is created and a
    ``job.remap_proposed`` webhook is emitted.
  - The human operator reviews and approves via the HITL UI.

Cost guardrail:
  - CostTracker hard cap $0.30 inside RemapperAgent (per-agent).
  - BudgetEnforcer pre-flight check (op_type='llm') for job-level cap.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from temporalio import activity
from temporalio.exceptions import ApplicationError

from open_banca_domain.entities.bank_map import BankMap
from open_banca_domain.entities.breakage_event import BreakageEvent
from open_banca_domain.entities.remap_proposal import RemapProposal, RemapStatus

logger = logging.getLogger(__name__)

# Proposal TTL — human must act within 24 h
_PROPOSAL_TTL_HOURS = 24


class RemapperAgentInput(BaseModel):
    """Input for RemapperAgentActivity."""

    job_id: str = Field(description="Parent job identifier")
    bank_id: str = Field(description="Bank whose map needs repair")
    breakage_hash: str = Field(
        description="SHA-256 hash of the BreakageEvent that triggered the remap"
    )
    run_id: str = Field(description="Unique run ID for idempotency")
    current_map: BankMap = Field(description="Existing (broken) map.json to repair")
    breakage_event: BreakageEvent = Field(description="Full BreakageEvent for context")
    proposal_id: str = Field(
        description="Proposal ID from JudgeActivity — included in webhook event"
    )
    judge_decision: str = Field(
        default="partial_remap",
        description="Judge decision string (for RemapProposal.judge_decision)",
    )
    sandbox_container_id: str = Field(
        description="Sandbox container with live browser for visual diff"
    )
    llm_budget_usd: float = Field(
        default=0.30,
        description="Maximum LLM spend for this remapping run (remapper cap $0.30)",
    )
    sensitive_data: dict[str, str] = Field(
        default_factory=dict,
        description="Credential placeholder → actual-value map (never logged)",
    )


class RemapperAgentResult(BaseModel):
    """Result from RemapperAgentActivity."""

    updated_map: BankMap = Field(
        description=(
            "In-memory patched map (computed, NOT persisted — v1 HITL; "
            "apply only after human approval)"
        )
    )
    llm_cost_usd: float = Field(description="Actual LLM spend for this run")
    diff_summary: str = Field(description="Human-readable summary of changes proposed")
    proposal_id: str = Field(description="RemapProposal ID created for HITL review")
    applied: bool = Field(
        default=False,
        description="Always False in v1 — map is never auto-applied (ADR-0013)",
    )


class RemapperAgentActivity:
    """RemapperAgentActivity class-based wrapper (unused, kept for compatibility)."""


@activity.defn(name="RemapperAgentActivity")
async def remapper_agent(input: RemapperAgentInput) -> RemapperAgentResult:
    """Run the Remapper agent to propose a repair patch for a broken map.json.

    Flow:
    1. BudgetEnforcer pre-flight check (op_type='llm', est $0.30).
    2. RemapperAgent.remap() → RemapPatch.
    3. DryRunResult = validate_patch(patch, current_map).
    4. If dry-run fails → raise ApplicationError(non_retryable=True).
    5. v1 HITL-only: create RemapProposal + emit job.remap_proposed webhook.
    6. Return RemapperAgentResult(applied=False, proposal_id=...).

    Sends heartbeats every 30 s.

    Cost guardrail: aborts with ApplicationError(non_retryable=True) if budget
    is exceeded at BudgetEnforcer level or inside RemapperAgent.
    """
    activity.heartbeat("starting remapper_agent")

    # ------------------------------------------------------------------
    # BudgetEnforcer pre-flight (T26)
    # ------------------------------------------------------------------
    _check_budget(input.job_id, input.llm_budget_usd)

    # ------------------------------------------------------------------
    # RemapperAgent invocation
    # ------------------------------------------------------------------
    try:
        from open_banca_llm.mapper.errors import CostExceeded, WallclockExceeded
        from open_banca_llm.remapper.agent import RemapperAgent
        from open_banca_llm.remapper.dry_run import validate_patch
    except ImportError as exc:
        raise ApplicationError(
            f"open-banca-llm package not available: {exc}",
            non_retryable=True,
        ) from exc

    agent = RemapperAgent(cost_cap_usd=input.llm_budget_usd)

    activity.heartbeat("invoking remapper llm")
    try:
        result = await agent.remap(
            breakage_event=input.breakage_event,
            current_map=input.current_map,
            sensitive_data=input.sensitive_data,
        )
    except CostExceeded as exc:
        raise ApplicationError(
            f"Remapper LLM cost cap exceeded: {exc}",
            non_retryable=True,
        ) from exc
    except WallclockExceeded as exc:
        raise ApplicationError(
            f"Remapper wallclock cap exceeded: {exc}",
            non_retryable=True,
        ) from exc
    except Exception as exc:
        raise ApplicationError(
            f"RemapperAgent.remap() failed: {exc}",
            non_retryable=False,
        ) from exc

    patch = result.patch
    activity.heartbeat("running dry-run validation")

    # ------------------------------------------------------------------
    # Dry-run patch validation (T28 linter)
    # ------------------------------------------------------------------
    dry_run = validate_patch(patch, input.current_map)
    if not dry_run.ok:
        issues_str = "; ".join(dry_run.issues[:10])
        raise ApplicationError(
            f"RemapPatch dry-run failed ({len(dry_run.issues)} issues): {issues_str}",
            non_retryable=True,
        )

    # ------------------------------------------------------------------
    # Apply patch in-memory to build updated_map (not persisted in v1)
    # ------------------------------------------------------------------
    from open_banca_llm.remapper.dry_run import _apply_patch

    updated_map = _apply_patch(patch, input.current_map)

    # ------------------------------------------------------------------
    # v1 HITL-only: create RemapProposal (ADR-0013 amendment)
    # ------------------------------------------------------------------
    activity.heartbeat("creating remap proposal")
    import json

    proposal = RemapProposal(
        id=input.proposal_id,
        bank=input.bank_id,
        breakage_id=input.breakage_hash,
        judge_decision=input.judge_decision,
        confidence=patch.confidence,
        risk=str(patch.risk),
        patch_diff=json.dumps(patch.model_dump(), ensure_ascii=False),
        status=RemapStatus.PENDING,
        expires_at=datetime.now(UTC) + timedelta(hours=_PROPOSAL_TTL_HOURS),
    )

    _persist_proposal(proposal)

    # ------------------------------------------------------------------
    # Emit job.remap_proposed webhook
    # ------------------------------------------------------------------
    activity.heartbeat("emitting remap_proposed webhook")
    _emit_remap_proposed_webhook(
        job_id=input.job_id,
        proposal=proposal,
        llm_cost_usd=result.cost_usd,
    )

    diff_summary = (
        f"Remapper proposed {len(patch.new_steps)} step(s) at index "
        f"{patch.target_step_index} (confidence={patch.confidence:.2f}, "
        f"risk={patch.risk}): {patch.rationale[:200]}"
    )

    return RemapperAgentResult(
        updated_map=updated_map,
        llm_cost_usd=result.cost_usd,
        diff_summary=diff_summary,
        proposal_id=proposal.id,
        applied=False,  # v1: ALWAYS False — HITL required
    )


# ---------------------------------------------------------------------------
# Helpers (thin seams — injectable in tests via module-level override)
# ---------------------------------------------------------------------------


def _check_budget(job_id: str, est_cost_usd: float) -> None:
    """Pre-flight BudgetEnforcer check (T26).  No-op if guardrails not available."""
    try:
        from decimal import Decimal

        from open_banca_guardrails.budget import (
            BudgetEnforcer,  # type: ignore[import-untyped]
        )
        from open_banca_guardrails.errors import (
            BudgetExceeded,  # type: ignore[import-untyped]
        )
    except ImportError:
        logger.debug("BudgetEnforcer not available — skipping pre-flight check")
        return

    db_path = _get_guardrails_db_path()
    if db_path is None:
        return

    try:
        import sqlite3

        with sqlite3.connect(db_path) as conn:
            enforcer = BudgetEnforcer(conn=conn)
            try:
                enforcer.check(job_id=job_id, op_type="llm", est_cost=Decimal(str(est_cost_usd)))
            except BudgetExceeded as exc:
                raise ApplicationError(
                    f"BudgetEnforcer pre-flight check failed: {exc}",
                    non_retryable=True,
                ) from exc
    except ApplicationError:
        raise
    except Exception as exc:
        logger.warning("BudgetEnforcer check error (non-fatal): %s", exc)


def _get_guardrails_db_path() -> str | None:
    import os

    return os.environ.get("OPEN_BANCA_GUARDRAILS_DB_PATH")


def _persist_proposal(proposal: RemapProposal) -> None:
    """Persist RemapProposal to storage if available; log-only otherwise.

    This is a port-shaped seam — production wires in open-banca-storage.
    Tests override _persist_proposal_impl at module level.
    """
    _persist_proposal_impl(proposal)


def _default_persist_proposal(proposal: RemapProposal) -> None:
    logger.info(
        "RemapProposal created (no storage configured): id=%s bank=%s status=%s",
        proposal.id,
        proposal.bank,
        proposal.status,
    )


_persist_proposal_impl: Any = _default_persist_proposal


def _emit_remap_proposed_webhook(
    job_id: str,
    proposal: RemapProposal,
    llm_cost_usd: float,
) -> None:
    """Emit job.remap_proposed webhook if WebhookDispatcher is available."""
    _emit_webhook_impl(job_id, proposal, llm_cost_usd)


def _default_emit_webhook(job_id: str, proposal: RemapProposal, llm_cost_usd: float) -> None:
    logger.info(
        "job.remap_proposed webhook (no dispatcher configured): job_id=%s proposal_id=%s bank=%s",
        job_id,
        proposal.id,
        proposal.bank,
    )


_emit_webhook_impl: Any = _default_emit_webhook
