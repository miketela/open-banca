"""Tests for BudgetEnforcer — budget cap enforcement.

Covers:
  - test_budget_per_job_cap: cost > $0.50 → BudgetExceeded
  - test_incremental_scrape_cap: $0.10 hard cap for scrape ops
  - Hypothesis: enforcement agrees with arithmetic
"""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from open_banca_guardrails.budget import BudgetEnforcer
from open_banca_guardrails.db import open_db
from open_banca_guardrails.errors import BudgetExceeded

# ------------------------------------------------------------------ #
# test_budget_per_job_cap                                              #
# ------------------------------------------------------------------ #


def test_budget_per_job_cap_allows_under_cap(budget: BudgetEnforcer) -> None:
    """Cost exactly at cap should not raise."""
    budget.check("job-1", "llm", Decimal("0.50"))  # no raise


def test_budget_per_job_cap_raises_over_cap(budget: BudgetEnforcer) -> None:
    """Cost > $0.50 raises BudgetExceeded."""
    with pytest.raises(BudgetExceeded) as exc_info:
        budget.check("job-1", "llm", Decimal("0.51"))
    assert exc_info.value.job_id == "job-1"
    assert exc_info.value.op_type == "llm"
    assert exc_info.value.cap_usd == Decimal("0.50")


def test_budget_per_job_cap_accumulated(budget: BudgetEnforcer) -> None:
    """Accumulated cost > $0.50 across multiple records raises on next check."""
    budget.record("job-1", "llm", Decimal("0.30"))
    budget.record("job-1", "llm", Decimal("0.15"))
    # consumed = 0.45, adding 0.10 would be 0.55 > 0.50
    with pytest.raises(BudgetExceeded):
        budget.check("job-1", "llm", Decimal("0.10"))


def test_budget_per_job_cap_consumed_returns_new_total(
    budget: BudgetEnforcer,
) -> None:
    """record() returns the new cumulative total."""
    total = budget.record("job-2", "llm", Decimal("0.20"))
    assert total == Decimal("0.20")
    total = budget.record("job-2", "llm", Decimal("0.25"))
    assert total == Decimal("0.45")


def test_budget_per_job_cap_separate_jobs_independent(
    budget: BudgetEnforcer,
) -> None:
    """Budget caps are per-job — different jobs don't share a budget."""
    budget.record("job-A", "llm", Decimal("0.49"))
    # job-B should be unaffected
    budget.check("job-B", "llm", Decimal("0.50"))  # no raise


# ------------------------------------------------------------------ #
# test_incremental_scrape_cap                                           #
# ------------------------------------------------------------------ #


def test_incremental_scrape_cap_allows_at_cap(budget: BudgetEnforcer) -> None:
    """Scrape cost at exactly $0.10 should not raise."""
    budget.check("job-s1", "scrape", Decimal("0.10"))


def test_incremental_scrape_cap_raises_over(budget: BudgetEnforcer) -> None:
    """Scrape cost > $0.10 raises BudgetExceeded."""
    with pytest.raises(BudgetExceeded) as exc_info:
        budget.check("job-s1", "scrape", Decimal("0.11"))
    assert exc_info.value.cap_usd == Decimal("0.10")


def test_incremental_scrape_cap_accumulated(budget: BudgetEnforcer) -> None:
    """Accumulated scrape cost triggers cap."""
    budget.record("job-s2", "scrape", Decimal("0.07"))
    with pytest.raises(BudgetExceeded):
        budget.check("job-s2", "scrape", Decimal("0.05"))


# ------------------------------------------------------------------ #
# Hypothesis: enforcement agrees with arithmetic                        #
# ------------------------------------------------------------------ #


@given(
    costs=st.lists(
        st.decimals(min_value=Decimal("0.01"), max_value=Decimal("0.20"), places=4),
        min_size=1,
        max_size=20,
    )
)
@settings(max_examples=200)
def test_hypothesis_check_iff_would_exceed(costs: list[Decimal]) -> None:
    """check() raises BudgetExceeded exactly when consumed + est_cost > cap.

    Invariant: check() must agree with the arithmetic predicate
    ``consumed + est_cost > cap`` for every call.  This property verifies
    the enforcement boundary is precise and unambiguous.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    conn = open_db(path)
    enforcer = BudgetEnforcer(
        conn=conn, llm_job_cap_usd=Decimal("0.50"), scrape_cap_usd=Decimal("0.10")
    )
    cap = Decimal("0.50")
    total = Decimal("0")

    for cost in costs:
        would_exceed = total + cost > cap
        raised = False
        try:
            enforcer.check("job-hypo", "llm", cost)
        except BudgetExceeded:
            raised = True

        # Enforcement must agree with the arithmetic
        assert raised == would_exceed, (
            f"Mismatch: total={total}, cost={cost}, cap={cap}, "
            f"would_exceed={would_exceed}, raised={raised}"
        )

        if not raised:
            enforcer.record("job-hypo", "llm", cost)
            total = enforcer.consumed("job-hypo", "llm")
