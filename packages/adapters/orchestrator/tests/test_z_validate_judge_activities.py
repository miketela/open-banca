"""Tests for ValidateActivity and JudgeActivity wiring (task 19).

All tests use OPEN_BANCA_TEST_MODEL=1 environment or direct TestModel injection
to avoid live LLM calls in CI.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from open_banca_domain.entities.breakage_event import BreakageEvent

from open_banca_orchestrator.activities.judge import JudgeInput, JudgeResult, judge
from open_banca_orchestrator.activities.parse_excel import TransactionRecord
from open_banca_orchestrator.activities.validate import (
    ValidateInput,
    ValidateResult,
    ValidationStatus,
    validate,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _make_txn(
    raw_id: str = "T001",
    date: str = "2024-01-15",
    amount: str = "50.00",
    currency: str = "USD",
) -> TransactionRecord:
    return TransactionRecord(
        raw_id=raw_id,
        date=date,
        description="Test txn",
        amount=amount,
        currency=currency,
        account_id="acc-001",
    )


def _make_breakage(job_id: str = "job-001") -> BreakageEvent:
    return BreakageEvent(
        job_id=job_id,
        step_index=1,
        step_type="click",
        error_class="element_missing",
        screenshot_ref="sha256:abc",
        dom_excerpt="<div>error page</div>",
        occurred_at=_NOW,
    )


# ---------------------------------------------------------------------------
# ValidateActivity tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_activity_with_test_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """ValidateActivity runs without error when OPEN_BANCA_TEST_MODEL=1."""
    monkeypatch.setenv("OPEN_BANCA_TEST_MODEL", "1")

    txns = [_make_txn("T1"), _make_txn("T2", date="2024-01-16", amount="60.00")]
    inp = ValidateInput(
        job_id="job-001",
        account_id="acc-001",
        transactions=txns,
        payload_hash="abc123",
        reported_balance="110.00",
    )

    result = await validate(inp)
    assert isinstance(result, ValidateResult)
    assert result.status in (
        ValidationStatus.ok,
        ValidationStatus.warnings,
        ValidationStatus.failed,
    )
    assert isinstance(result.validated_count, int)


@pytest.mark.asyncio
async def test_validate_balance_mismatch_no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Balance mismatch is a heuristic fail — no LLM needed, breakage_detected=True."""
    monkeypatch.setenv("OPEN_BANCA_TEST_MODEL", "1")

    txns = [_make_txn("T1", amount="50.00"), _make_txn("T2", amount="60.00")]
    inp = ValidateInput(
        job_id="job-002",
        account_id="acc-002",
        transactions=txns,
        payload_hash="def456",
        reported_balance="100.00",  # 110 != 100 → mismatch
    )

    result = await validate(inp)
    assert result.status == ValidationStatus.failed
    assert result.breakage_detected is True
    assert any(i.code == "balance_mismatch" for i in result.issues)


# ---------------------------------------------------------------------------
# JudgeActivity tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_activity_with_test_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """JudgeActivity runs without error when OPEN_BANCA_TEST_MODEL=1."""
    monkeypatch.setenv("OPEN_BANCA_TEST_MODEL", "1")

    inp = JudgeInput(
        job_id="job-001",
        breakage_event=_make_breakage(),
        breakage_hash="hash123",
    )

    result = await judge(inp)
    assert isinstance(result, JudgeResult)
    assert result.route == "human_required", (
        f"v1 HITL invariant violated: route={result.route!r}"
    )


@pytest.mark.asyncio
async def test_judge_activity_result_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """JudgeResult has required fields with correct types."""
    monkeypatch.setenv("OPEN_BANCA_TEST_MODEL", "1")

    inp = JudgeInput(
        job_id="job-002",
        breakage_event=_make_breakage("job-002"),
        breakage_hash="hash456",
        dom_excerpt="<div>something failed</div>",
    )

    result = await judge(inp)
    assert isinstance(result.route, str)
    assert isinstance(result.confidence, float)
    assert 0.0 <= result.confidence <= 1.0
    assert result.risk in ("low", "med", "high")
    assert isinstance(result.rationale, str)


@pytest.mark.asyncio
async def test_judge_v1_always_human_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """v1 ADR-0013 amendment: JudgeActivity ALWAYS returns route=human_required."""
    monkeypatch.setenv("OPEN_BANCA_TEST_MODEL", "1")

    error_classes = ["element_missing", "layout_changed", "http_error", "timeout"]
    for error_class in error_classes:
        event = BreakageEvent(
            job_id="job-003",
            step_index=0,
            step_type="navigate",
            error_class=error_class,
            screenshot_ref="sha256:xyz",
            dom_excerpt="<div>broken</div>",
            occurred_at=_NOW,
        )
        inp = JudgeInput(
            job_id="job-003",
            breakage_event=event,
            breakage_hash=f"hash-{error_class}",
        )
        result = await judge(inp)
        assert result.route == "human_required", (
            f"v1 HITL violated for error_class={error_class!r}: route={result.route!r}"
        )
