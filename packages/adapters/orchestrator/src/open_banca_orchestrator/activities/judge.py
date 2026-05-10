"""JudgeActivity — evaluate breakage events and decide next action.

PLACEHOLDER — wired in task 19 (Validator/Judge AI agents).

Retry policy (orchestrator.md §Inventario):
  - 1 attempt (judgement is idempotent on the BreakageEvent hash).
  - start-to-close timeout: 30 s.

Idempotency key: SHA-256 hash of the BreakageEvent.

Uses DeepSeek V3 vision model to evaluate screenshot diffs and decide whether
to auto-approve a remap, request human review, or mark the job as recoverable.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field
from temporalio import activity


class JudgeDecision(StrEnum):
    """Decision returned by the Judge agent."""

    ok = "ok"
    remap_proposed = "remap_proposed"
    human_required = "human_required"
    unrecoverable = "unrecoverable"


class BreakageEvent(BaseModel):
    """Description of a detected breakage for the Judge to evaluate."""

    job_id: str
    bank_id: str
    account_id: str
    breakage_type: str = Field(
        description="Type of breakage: layout_changed, element_missing, etc."
    )
    screenshot_path: str | None = Field(
        default=None,
        description="Path to screenshot inside sandbox container for visual analysis",
    )
    error_detail: str | None = None


class JudgeInput(BaseModel):
    """Input for JudgeActivity."""

    job_id: str = Field(description="Unique job identifier")
    breakage_event: BreakageEvent = Field(description="Breakage event to evaluate")
    breakage_hash: str = Field(
        description="SHA-256 hash of the BreakageEvent for idempotency"
    )


class JudgeResult(BaseModel):
    """Result from JudgeActivity."""

    decision: JudgeDecision
    proposal_id: str | None = Field(
        default=None,
        description="Remap proposal ID when decision==remap_proposed",
    )
    reasoning: str = Field(
        default="",
        description="Human-readable reasoning for the decision",
    )


class JudgeActivity:
    """JudgeActivity class-based wrapper."""


@activity.defn(name="JudgeActivity")
async def judge(input: JudgeInput) -> JudgeResult:  # noqa: A002
    """Evaluate a breakage event and decide the recovery path.

    PLACEHOLDER — wired in task 19 (Validator/Judge AI agents).

    TODO: integrate with PydanticAI + LiteLLM DeepSeek V3 vision (task 19).
    TODO: enforce $0.50/job LLM cost guardrail before invoking LLM.
    TODO: check circuit breaker state before invoking (orchestrator.md §Garantías).
    """
    raise NotImplementedError(
        "JudgeActivity not implemented — PLACEHOLDER, wired in task 19"
    )
