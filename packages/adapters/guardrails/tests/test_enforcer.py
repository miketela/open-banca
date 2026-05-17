"""Tests for GuardrailEnforcer — orchestration of all guardrail checks.

Covers:
  - test_budget_exceeded_emits_failure: BudgetCheckActivity raises
    ApplicationError with type="budget_exceeded"
  - End-to-end check/record flow through the enforcer
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest
from temporalio.exceptions import ApplicationError

import open_banca_guardrails.activity as activity_module
from open_banca_guardrails.activity import BudgetCheckActivity, BudgetCheckInput
from open_banca_guardrails.budget import BudgetEnforcer
from open_banca_guardrails.circuit_breaker import CircuitBreaker
from open_banca_guardrails.enforcer import GuardrailEnforcer, OperationType
from open_banca_guardrails.errors import BudgetExceeded, CircuitOpen, RateLimited
from open_banca_guardrails.rate_limits import RateLimitTracker

from .conftest import FakeClock

# ------------------------------------------------------------------ #
# Enforcer check routing                                               #
# ------------------------------------------------------------------ #


def test_enforcer_allows_llm_under_cap(enforcer: GuardrailEnforcer) -> None:
    """LLM operation under budget cap passes through."""
    enforcer.check("job-1", "banco_general", OperationType.LLM, Decimal("0.40"))


def test_enforcer_raises_llm_over_cap(enforcer: GuardrailEnforcer) -> None:
    """LLM operation over budget cap raises BudgetExceeded."""
    with pytest.raises(BudgetExceeded):
        enforcer.check("job-1", "banco_general", OperationType.LLM, Decimal("0.51"))


def test_enforcer_raises_scrape_over_cap(enforcer: GuardrailEnforcer) -> None:
    """Scrape operation over $0.10 raises BudgetExceeded."""
    with pytest.raises(BudgetExceeded):
        enforcer.check("job-2", "banco_general", OperationType.SCRAPE, Decimal("0.11"))


def test_enforcer_remap_rate_limit(enforcer: GuardrailEnforcer, clock: FakeClock) -> None:
    """4th remap raises RateLimited through the enforcer."""
    for _ in range(3):
        clock.tick(minutes=5)
        enforcer.record("job-3", "banco_general", OperationType.REMAP)

    clock.tick(minutes=5)
    with pytest.raises(RateLimited):
        enforcer.check_and_record_rate("banco_general", "remap")


def test_enforcer_circuit_open_login(enforcer: GuardrailEnforcer, clock: FakeClock) -> None:
    """After 2 login failures, circuit opens and check raises CircuitOpen."""
    enforcer.record(
        "job-4",
        "banco_general",
        OperationType.LOGIN,
        credential_ref="cred-1",
        login_success=False,
    )
    enforcer.record(
        "job-4",
        "banco_general",
        OperationType.LOGIN,
        credential_ref="cred-1",
        login_success=False,
    )
    with pytest.raises(CircuitOpen):
        enforcer.check(
            "job-4",
            "banco_general",
            OperationType.LOGIN,
            credential_ref="cred-1",
        )


def test_enforcer_login_success_resets_circuit(
    enforcer: GuardrailEnforcer, clock: FakeClock
) -> None:
    """Successful login after one failure resets; circuit stays CLOSED."""
    enforcer.record(
        "job-5",
        "banco_general",
        OperationType.LOGIN,
        credential_ref="cred-2",
        login_success=False,
    )
    enforcer.record(
        "job-5",
        "banco_general",
        OperationType.LOGIN,
        credential_ref="cred-2",
        login_success=True,
    )
    # After success reset, one failure shouldn't open
    enforcer.record(
        "job-5",
        "banco_general",
        OperationType.LOGIN,
        credential_ref="cred-2",
        login_success=False,
    )
    enforcer.check(
        "job-5",
        "banco_general",
        OperationType.LOGIN,
        credential_ref="cred-2",
    )  # no raise


# ------------------------------------------------------------------ #
# test_budget_exceeded_emits_failure (activity level)                  #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_budget_exceeded_emits_failure(
    mem_db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BudgetCheckActivity raises ApplicationError(type='budget_exceeded').

    This is the contract the Temporal workflow consumes to set
    job.failed reason="budget_exceeded".
    """
    # Pre-load $0.48 consumed for this job — adding $0.05 will exceed $0.50
    budget_seed = BudgetEnforcer(conn=mem_db, llm_job_cap_usd=Decimal("0.50"))
    budget_seed.record("job-act-1", "llm", Decimal("0.48"))

    # Build a pre-seeded enforcer and patch the factory used by the activity
    preloaded = GuardrailEnforcer(
        budget=BudgetEnforcer(conn=mem_db, llm_job_cap_usd=Decimal("0.50")),
        rate=RateLimitTracker(conn=mem_db),
        circuit=CircuitBreaker(conn=mem_db),
    )

    def _mock_from_settings(
        cls: object,
        settings: object = None,
        clock: object = None,
        db_path: object = None,
    ) -> GuardrailEnforcer:
        return preloaded

    monkeypatch.setattr(
        activity_module.GuardrailEnforcer,
        "from_settings",
        classmethod(_mock_from_settings),
    )

    input_data = BudgetCheckInput(
        job_id="job-act-1",
        bank="banco_general",
        op_type="llm",
        est_cost_usd="0.05",  # 0.48 + 0.05 = 0.53 > 0.50
    )
    with pytest.raises(ApplicationError) as exc_info:
        await BudgetCheckActivity(input_data)

    err = exc_info.value
    assert err.type == "budget_exceeded"
    assert err.non_retryable is True
