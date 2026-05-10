"""ValidateActivity — validate normalized transaction data against business rules.

PLACEHOLDER — wired in task 19 (Validator/Judge AI agents).

Retry policy (orchestrator.md §Inventario):
  - 2 attempts.
  - start-to-close timeout: 60 s.

Idempotency key: SHA-256 hash of the normalized payload.

Uses DeepSeek V3 as the validation LLM (cheap text model, per CLAUDE.md tech stack).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field
from temporalio import activity

from open_banca_orchestrator.activities.parse_excel import TransactionRecord


class ValidationStatus(StrEnum):
    """Outcome of a validation pass."""

    ok = "ok"
    warnings = "warnings"
    failed = "failed"


class ValidationIssue(BaseModel):
    """A single validation issue reported by the Validator agent."""

    code: str = Field(description="Machine-readable issue code")
    message: str = Field(description="Human-readable description")
    row_index: int | None = Field(default=None, description="Row index if issue is row-specific")


class ValidateInput(BaseModel):
    """Input for ValidateActivity."""

    job_id: str = Field(description="Unique job identifier")
    account_id: str = Field(description="Account the transactions belong to")
    transactions: list[TransactionRecord] = Field(
        description="Parsed transactions to validate"
    )
    payload_hash: str = Field(
        description="SHA-256 hash of the payload for idempotency"
    )


class ValidateResult(BaseModel):
    """Result from ValidateActivity."""

    status: ValidationStatus
    issues: list[ValidationIssue] = Field(default_factory=list)
    validated_count: int = Field(description="Number of transactions that passed validation")


class ValidateActivity:
    """ValidateActivity class-based wrapper."""


@activity.defn(name="ValidateActivity")
async def validate(input: ValidateInput) -> ValidateResult:  # noqa: A002
    """Validate normalized transactions using DeepSeek V3 Validator agent.

    PLACEHOLDER — wired in task 19 (Validator/Judge AI agents).

    TODO: integrate with PydanticAI + LiteLLM DeepSeek V3 (task 19).
    TODO: implement business rule checks (date ranges, balance continuity, etc.).
    TODO: enforce $0.50/job LLM cost guardrail before invoking LLM.
    """
    raise NotImplementedError(
        "ValidateActivity not implemented — PLACEHOLDER, wired in task 19"
    )
