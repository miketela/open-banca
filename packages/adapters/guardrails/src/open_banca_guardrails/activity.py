"""Temporal activity for pre-flight guardrail checks.

``BudgetCheckActivity`` is registered with the Temporal worker (task-12).
It is invoked before every LLM activity to enforce the budget cap.

On violation, it raises ``temporalio.exceptions.ApplicationError`` with
``type="budget_exceeded"`` (non-retryable), which the workflow maps to
``job.failed reason="budget_exceeded"``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from temporalio import activity
from temporalio.exceptions import ApplicationError

from open_banca_guardrails.config import get_settings
from open_banca_guardrails.enforcer import GuardrailEnforcer
from open_banca_guardrails.errors import BudgetExceeded, CircuitOpen, RateLimited


@dataclass
class BudgetCheckInput:
    """Input for ``BudgetCheckActivity``.

    Attributes:
        job_id: The current job identifier.
        bank: Bank identifier.
        op_type: Operation type (``"llm"`` or ``"scrape"``).
        est_cost_usd: Estimated cost of the upcoming LLM call (USD as string
            to avoid float serialization issues).
        credential_ref: Credential reference (required for login checks).
    """

    job_id: str
    bank: str
    op_type: str
    est_cost_usd: str = "0"
    credential_ref: str = ""


@activity.defn(name="budget_check")
async def BudgetCheckActivity(input: BudgetCheckInput) -> None:  # noqa: N802
    """Pre-flight guardrail check activity.

    Raises a non-retryable ``ApplicationError`` on any guardrail violation so
    that the calling workflow can transition the job to ``failed``.

    The enforcer is constructed from settings on each invocation (stateless
    activity pattern — Temporal may run this on any worker).

    Args:
        input: ``BudgetCheckInput`` with job and operation details.

    Raises:
        ApplicationError: With type ``"budget_exceeded"``, ``"rate_limited"``,
            or ``"circuit_open"`` for the respective violations.
    """
    settings = get_settings()
    enforcer = GuardrailEnforcer.from_settings(settings)
    est_cost = Decimal(input.est_cost_usd)

    try:
        enforcer.check(
            job_id=input.job_id,
            bank=input.bank,
            op_type=input.op_type,
            est_cost=est_cost,
            credential_ref=input.credential_ref,
        )
    except BudgetExceeded as exc:
        raise ApplicationError(
            str(exc),
            type="budget_exceeded",
            non_retryable=True,
        ) from exc
    except RateLimited as exc:
        raise ApplicationError(
            str(exc),
            type="rate_limited",
            non_retryable=True,
        ) from exc
    except CircuitOpen as exc:
        raise ApplicationError(
            str(exc),
            type="circuit_open",
            non_retryable=True,
        ) from exc
