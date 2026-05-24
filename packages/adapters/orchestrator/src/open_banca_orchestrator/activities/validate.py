"""ValidateActivity — validate normalized transaction data via ValidatorAgent.

Retry policy (orchestrator.md §Inventario):
  - 2 attempts.
  - start-to-close timeout: 60 s.

Idempotency key: SHA-256 hash of the normalized payload.

Uses DeepSeek V3 as the validation LLM (cheap text model, per CLAUDE.md tech stack).
In tests, inject a PydanticAI TestModel by setting OPEN_BANCA_TEST_MODEL=1 or
by constructing the activity with an explicit model override.
"""

from __future__ import annotations

import os
from decimal import Decimal
from enum import StrEnum
from typing import Any

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
    transactions: list[TransactionRecord] = Field(description="Parsed transactions to validate")
    payload_hash: str = Field(description="SHA-256 hash of the payload for idempotency")
    reported_balance: str | None = Field(
        default=None,
        description="Reported balance as decimal string, for heuristic balance check",
    )


class ValidateResult(BaseModel):
    """Result from ValidateActivity."""

    status: ValidationStatus
    issues: list[ValidationIssue] = Field(default_factory=list)
    validated_count: int = Field(description="Number of transactions that passed validation")
    breakage_detected: bool = Field(
        default=False,
        description="True if validation failure is severe enough to trigger Judge/remap",
    )


class ValidateActivity:
    """ValidateActivity class-based wrapper.

    Args:
        model: PydanticAI model override (for testing). If None, uses DeepSeek
               or the test model when OPEN_BANCA_TEST_MODEL env var is set.
    """

    def __init__(self, model: Any = None) -> None:
        self._model = model


def _get_validator_model(override: Any) -> Any:
    """Return the model to use: explicit override → env flag → production default."""
    if override is not None:
        return override
    if os.environ.get("OPEN_BANCA_TEST_MODEL") == "1":
        from pydantic_ai.models.test import TestModel

        return TestModel()
    return None  # ValidatorAgent will use its default (deepseek)


@activity.defn(name="ValidateActivity")
async def validate(input: ValidateInput) -> ValidateResult:
    """Validate normalized transactions using DeepSeek V3 Validator agent.

    Heuristic checks run first (balance mismatch, duplicates, currency mismatch,
    date holes). LLM fallback only for ambiguous cases.
    Cost cap: $0.05/call.

    If validation fails with hard issues, breakage_detected=True signals the
    workflow to invoke JudgeActivity.

    NOTE: pydantic_ai import is deferred to avoid beartype hook side effects
    in the Temporal sandbox. Empty transaction sets are checked before import.
    """
    # Fast-path: empty transaction set is always a hard failure — no LLM needed.
    # Checking this BEFORE importing open_banca_llm avoids loading pydantic_ai
    # (and its beartype hooks) in Temporal sandbox test environments.
    if not input.transactions:
        return ValidateResult(
            status=ValidationStatus.failed,
            issues=[
                ValidationIssue(
                    code="empty_transaction_set",
                    message="No transactions provided for validation",
                )
            ],
            validated_count=0,
            breakage_detected=True,
        )

    from open_banca_llm.validator.agent import (
        CostCapExceeded,
        TransactionInput,
        ValidationVerdict,
        ValidatorAgent,
    )

    # Convert orchestrator TransactionRecord → llm TransactionInput
    txn_inputs = [
        TransactionInput(
            raw_id=t.raw_id,
            date=t.date,
            description=t.description,
            amount=t.amount,
            currency=t.currency,
            account_id=t.account_id,
        )
        for t in input.transactions
    ]

    reported_balance = Decimal(input.reported_balance) if input.reported_balance else None

    model = _get_validator_model(None)
    agent = ValidatorAgent(model=model)

    try:
        report = await agent.validate(txn_inputs, reported_balance=reported_balance)
    except CostCapExceeded as exc:
        activity.logger.warning("ValidateActivity: cost cap exceeded: %s", exc)
        return ValidateResult(
            status=ValidationStatus.failed,
            issues=[
                ValidationIssue(
                    code="cost_cap_exceeded",
                    message=str(exc),
                )
            ],
            validated_count=0,
            breakage_detected=True,
        )

    # Map verdict → ValidationStatus
    if report.verdict == ValidationVerdict.valid:
        status = ValidationStatus.ok
        breakage_detected = False
    elif report.verdict == ValidationVerdict.warn:
        status = ValidationStatus.warnings
        breakage_detected = False
    else:  # fail
        status = ValidationStatus.failed
        breakage_detected = True

    issues = [
        ValidationIssue(code=i.code, message=i.message, row_index=i.row_index)
        for i in report.issues
    ]

    return ValidateResult(
        status=status,
        issues=issues,
        validated_count=len(input.transactions) if status != ValidationStatus.failed else 0,
        breakage_detected=breakage_detected,
    )
