"""Tests for ValidatorAgent — heuristic checks and LLM fallback path.

Test strategy:
  - Unit tests using PydanticAI TestModel (no live LLM calls).
  - Heuristic checks run synchronously (no model needed).
  - LLM path uses Agent.override(model=TestModel) — zero cost, deterministic.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic_ai.models.test import TestModel

from open_banca_llm.validator.agent import (
    CostCapExceeded,
    TransactionInput,
    ValidationVerdict,
    ValidatorAgent,
    _compute_cost,
    _heuristic_checks,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_txn(
    raw_id: str = "T001",
    date: str = "2024-01-15",
    amount: str = "100.00",
    currency: str = "USD",
    description: str = "Test",
) -> TransactionInput:
    return TransactionInput(
        raw_id=raw_id,
        date=date,
        amount=amount,
        currency=currency,
        description=description,
    )


# ---------------------------------------------------------------------------
# test_validator_heuristics: balance != sum → verdict=fail
# ---------------------------------------------------------------------------


def test_heuristic_balance_mismatch_yields_fail_issues() -> None:
    """Balance != sum(transactions) should produce a balance_mismatch issue."""
    txns = [
        _make_txn("T1", amount="50.00"),
        _make_txn("T2", amount="60.00"),
    ]
    # Reported balance is 100 but sum = 110 → mismatch of 10
    issues = _heuristic_checks(txns, Decimal("100.00"))
    codes = [i.code for i in issues]
    assert "balance_mismatch" in codes


@pytest.mark.asyncio
async def test_validator_balance_mismatch_verdict_fail() -> None:
    """ValidatorAgent with balance != sum should return verdict=fail without LLM."""
    txns = [
        _make_txn("T1", amount="50.00"),
        _make_txn("T2", amount="60.00"),
    ]
    agent = ValidatorAgent()
    report = await agent.validate(txns, reported_balance=Decimal("100.00"))
    assert report.verdict == ValidationVerdict.fail
    assert any(i.code == "balance_mismatch" for i in report.issues)
    assert report.llm_used is False


@pytest.mark.asyncio
async def test_validator_duplicates_yield_fail() -> None:
    """Duplicate raw_ids should produce verdict=fail."""
    txns = [_make_txn("T1"), _make_txn("T1")]  # same raw_id
    agent = ValidatorAgent()
    report = await agent.validate(txns)
    assert report.verdict == ValidationVerdict.fail
    assert any(i.code == "duplicate_transaction_id" for i in report.issues)
    assert report.llm_used is False


@pytest.mark.asyncio
async def test_validator_currency_mismatch_yields_fail() -> None:
    """Mixed currencies should produce verdict=fail."""
    txns = [
        _make_txn("T1", currency="USD"),
        _make_txn("T2", currency="EUR"),
    ]
    agent = ValidatorAgent()
    report = await agent.validate(txns)
    assert report.verdict == ValidationVerdict.fail
    assert any(i.code == "currency_mismatch" for i in report.issues)


@pytest.mark.asyncio
async def test_validator_no_issues_yields_valid() -> None:
    """Clean transactions with no issues should return verdict=valid without LLM."""
    txns = [
        _make_txn("T1", date="2024-01-15", amount="50.00"),
        _make_txn("T2", date="2024-01-16", amount="60.00"),
    ]
    agent = ValidatorAgent()
    report = await agent.validate(txns, reported_balance=Decimal("110.00"))
    assert report.verdict == ValidationVerdict.valid
    assert report.llm_used is False


# ---------------------------------------------------------------------------
# test_validator_llm_path: ambiguous case triggers LLM with TestModel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validator_llm_path_called_for_ambiguous() -> None:
    """Date gap (soft issue only) should trigger LLM call with TestModel."""
    # Create two transactions with a 90-day gap → triggers date_gap issue
    txns = [
        _make_txn("T1", date="2024-01-01", amount="50.00"),
        _make_txn("T2", date="2024-04-01", amount="60.00"),  # 91-day gap
    ]

    # TestModel returns a fixed structured output
    test_model = TestModel()

    agent = ValidatorAgent(model=test_model)
    report = await agent.validate(txns)

    # LLM was used (ambiguous path)
    assert report.llm_used is True
    # verdict comes from TestModel (returns valid/warn for test output)
    assert report.verdict in (
        ValidationVerdict.valid,
        ValidationVerdict.warn,
        ValidationVerdict.fail,
    )


@pytest.mark.asyncio
async def test_validator_llm_path_uses_injected_model() -> None:
    """Agent with override_model uses the injected TestModel, not a real API."""
    txns = [
        _make_txn("T1", date="2024-01-01"),
        _make_txn("T2", date="2024-04-15"),  # 105-day gap → ambiguous
    ]

    test_model = TestModel()
    agent = ValidatorAgent(model=test_model)
    report = await agent.validate(txns)

    # Must have used LLM (not raise an API error)
    assert report.llm_used is True


# ---------------------------------------------------------------------------
# test_cost_caps: validator > $0.05 → abort
# ---------------------------------------------------------------------------


def test_compute_cost_is_correct() -> None:
    """Sanity check: 1M input + 1M output tokens should equal published rates."""
    cost = _compute_cost(1_000_000, 1_000_000)
    # $0.27 + $1.10 = $1.37
    assert abs(float(cost) - 1.37) < 0.01


@pytest.mark.asyncio
async def test_validator_cost_cap_exceeded_on_huge_input() -> None:
    """With a tiny cost cap and a large input, CostCapExceeded should be raised."""
    # Create many transactions with date gaps to trigger ambiguous/LLM path
    txns: list[TransactionInput] = []
    for i in range(5):
        txns.append(_make_txn(f"T{i}", date="2024-01-01", amount="10.00"))
    # Add a gap to trigger ambiguous path
    txns.append(_make_txn("T99", date="2024-09-01", amount="10.00"))

    # Cap so small it fires on estimated tokens
    agent = ValidatorAgent(cost_cap_usd=Decimal("0.000001"))

    with pytest.raises(CostCapExceeded) as exc_info:
        await agent.validate(txns)

    assert exc_info.value.cap == Decimal("0.000001")
