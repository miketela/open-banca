"""GuardrailEnforcer — central orchestration of budget, rate, and circuit checks.

This is the single entry point for all guardrail enforcement.  Callers invoke
``check()`` before an operation (raises on violation) and ``record()`` after
successful completion (persists the actual cost).

``OperationType`` enumerates the supported operation kinds.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from open_banca_domain.ports.clock_port import ClockPort

from open_banca_guardrails.budget import BudgetEnforcer
from open_banca_guardrails.circuit_breaker import CircuitBreaker
from open_banca_guardrails.config import GuardrailSettings, get_settings
from open_banca_guardrails.db import open_db
from open_banca_guardrails.rate_limits import RateLimitTracker


class OperationType(StrEnum):
    """Supported operation types for guardrail enforcement."""

    LLM = "llm"
    SCRAPE = "scrape"
    REMAP = "remap"
    MAPPING = "mapping"
    LOGIN = "login"


class GuardrailEnforcer:
    """Orchestrates budget, rate-limit, and circuit-breaker enforcement.

    Use the class-method constructor ``from_settings()`` for production, or
    supply individual components directly in tests.

    Args:
        budget: ``BudgetEnforcer`` instance.
        rate: ``RateLimitTracker`` instance.
        circuit: ``CircuitBreaker`` instance.
    """

    def __init__(
        self,
        budget: BudgetEnforcer,
        rate: RateLimitTracker,
        circuit: CircuitBreaker,
    ) -> None:
        self._budget = budget
        self._rate = rate
        self._circuit = circuit

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_settings(
        cls,
        settings: GuardrailSettings | None = None,
        clock: ClockPort | None = None,
        db_path: Path | None = None,
    ) -> GuardrailEnforcer:
        """Create an enforcer backed by a SQLite DB from settings.

        Args:
            settings: Guardrail settings (reads env vars when None).
            clock: Clock implementation for testing.
            db_path: Override DB path (for tests — use in-memory or tmp).

        Returns:
            Configured ``GuardrailEnforcer``.
        """
        cfg = settings or get_settings()
        resolved_path = db_path or cfg.db_path
        conn = open_db(resolved_path)
        budget = BudgetEnforcer(
            conn=conn,
            llm_job_cap_usd=cfg.llm_job_cap_usd,
            scrape_cap_usd=cfg.scrape_cap_usd,
            max_tokens_per_job=cfg.max_tokens_per_job,
        )
        rate = RateLimitTracker(
            conn=conn,
            remap_limit_24h=cfg.remap_limit_24h,
            mapping_limit_24h=cfg.mapping_limit_24h,
            mapping_limit_override=cfg.mapping_limit_override,
            clock=clock,
        )
        circuit = CircuitBreaker(
            conn=conn,
            login_fail_limit=cfg.login_fail_limit,
            cooldown_hours=cfg.cb_cooldown_hours,
            clock=clock,
        )
        return cls(budget=budget, rate=rate, circuit=circuit)

    # ------------------------------------------------------------------
    # Main interface
    # ------------------------------------------------------------------

    def check(
        self,
        job_id: str,
        bank: str,
        op_type: str,
        est_cost: Decimal = Decimal("0"),
        credential_ref: str = "",
    ) -> None:
        """Pre-flight check: raises if any guardrail would be violated.

        Runs all applicable checks in order:
          1. Circuit breaker (if op_type == "login").
          2. Rate limit (if op_type in {"remap", "mapping"}).
          3. Budget cap (if op_type in {"llm", "scrape"} or est_cost > 0).

        Args:
            job_id: Job identifier.
            bank: Bank identifier.
            op_type: Operation type (``OperationType`` string).
            est_cost: Estimated cost in USD (0 for non-cost operations).
            credential_ref: Credential reference (required for ``"login"`` checks).

        Raises:
            CircuitOpen: If the circuit is open for this bank/credential.
            RateLimited: If rate limit is exceeded.
            BudgetExceeded: If budget cap would be exceeded.
        """
        # 1. Circuit breaker check
        if op_type == OperationType.LOGIN and credential_ref:
            self._circuit.check(bank, credential_ref)

        # 2. Rate limit check (record-and-check semantic for remap/mapping)
        if op_type in (OperationType.REMAP, OperationType.MAPPING):
            self._rate.check(bank, op_type)

        # 3. Budget check
        if est_cost > Decimal("0") or op_type in (OperationType.LLM, OperationType.SCRAPE):
            self._budget.check(job_id, op_type, est_cost)

    def record(  # noqa: PLR0913
        self,
        job_id: str,
        bank: str,
        op_type: str,
        actual_cost: Decimal = Decimal("0"),
        credential_ref: str = "",
        login_success: bool | None = None,
    ) -> None:
        """Post-operation recording: persist actual cost and update counters.

        Args:
            job_id: Job identifier.
            bank: Bank identifier.
            op_type: Operation type.
            actual_cost: Actual cost incurred (USD).
            credential_ref: Credential reference (for circuit breaker updates).
            login_success: True/False for login ops; None for non-login ops.
        """
        # Record cost if applicable
        if actual_cost > Decimal("0"):
            self._budget.record(job_id, op_type, actual_cost)

        # Record rate-limit events for remap/mapping
        if op_type in (OperationType.REMAP, OperationType.MAPPING):
            self._rate.record_and_check(bank, op_type)

        # Update circuit breaker for login results
        if op_type == OperationType.LOGIN and credential_ref and login_success is not None:
            if login_success:
                self._circuit.record_success(bank, credential_ref)
            else:
                self._circuit.record_failure(bank, credential_ref)

    def check_and_record_rate(self, bank: str, op_type: str) -> None:
        """Convenience: check + atomically record a rate-limited operation.

        Equivalent to calling ``record_and_check`` on the rate tracker directly.

        Args:
            bank: Bank identifier.
            op_type: Operation type (``"remap"`` or ``"mapping"``).

        Raises:
            RateLimited: If the operation would exceed its rate limit.
        """
        self._rate.record_and_check(bank, op_type)
