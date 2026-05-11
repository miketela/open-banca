"""Guardrail error hierarchy.

All errors are non-retryable from the Temporal activity perspective.
"""

from __future__ import annotations

from decimal import Decimal


class GuardrailError(Exception):
    """Base error for all guardrail failures."""


class BudgetExceeded(GuardrailError):
    """Raised when accumulated cost for a job exceeds its cap.

    Args:
        job_id: The job that exceeded its budget.
        consumed_usd: Total cost consumed so far (USD).
        cap_usd: Configured budget cap (USD).
        op_type: The operation type that triggered the breach.
    """

    def __init__(
        self,
        job_id: str,
        consumed_usd: Decimal,
        cap_usd: Decimal,
        op_type: str,
    ) -> None:
        self.job_id = job_id
        self.consumed_usd = consumed_usd
        self.cap_usd = cap_usd
        self.op_type = op_type
        super().__init__(
            f"Job {job_id} cost ${consumed_usd:.4f} exceeds cap ${cap_usd:.2f} "
            f"for op={op_type}"
        )


class RateLimited(GuardrailError):
    """Raised when an operation exceeds the configured rate limit.

    Args:
        bank: Bank identifier.
        op_type: The rate-limited operation.
        limit: Maximum allowed count in the window.
        window_hours: Rate limit window in hours.
        retry_after_seconds: Approximate seconds until the oldest event expires.
    """

    def __init__(
        self,
        bank: str,
        op_type: str,
        limit: int,
        window_hours: int,
        retry_after_seconds: float = 0.0,
    ) -> None:
        self.bank = bank
        self.op_type = op_type
        self.limit = limit
        self.window_hours = window_hours
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Rate limit exceeded for bank={bank} op={op_type}: "
            f"max {limit} per {window_hours}h, retry in {retry_after_seconds:.0f}s"
        )


class CircuitOpen(GuardrailError):
    """Raised when the circuit breaker is OPEN for a bank/credential pair.

    Args:
        bank: Bank identifier.
        credential_ref: Credential reference string.
        cooldown_remaining_seconds: Remaining cooldown period in seconds.
    """

    def __init__(
        self,
        bank: str,
        credential_ref: str,
        cooldown_remaining_seconds: float,
    ) -> None:
        self.bank = bank
        self.credential_ref = credential_ref
        self.cooldown_remaining_seconds = cooldown_remaining_seconds
        super().__init__(
            f"Circuit OPEN for bank={bank} credential={credential_ref}: "
            f"retry in {cooldown_remaining_seconds:.0f}s"
        )
