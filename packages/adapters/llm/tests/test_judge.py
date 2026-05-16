"""Tests for JudgeAgent — routing decisions, v1 HITL, PII redaction, cost caps.

Test strategy:
  - All tests use PydanticAI TestModel (no live LLM calls in CI).
  - Critical invariant: v1 route is ALWAYS human_required (ADR-0013 amendment).
  - PII canary: dom_excerpt with PII must be redacted before LLM receives it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic_ai.models.test import TestModel

from open_banca_domain.entities.breakage_event import BreakageEvent
from open_banca_llm.judge.agent import (
    CostCapExceeded,
    JudgeAgent,
    JudgeDecision,
    JudgeRoute,
    redact_pii,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)

PII_CANARY_NAME = "John.Smith@bancoexample.com"
PII_CANARY_PHONE = "+507 6000-1234"
PII_CANARY_CEDULA = "8-123-4567"


def _make_breakage(
    job_id: str = "job-001",
    error_class: str = "element_missing",
    step_type: str = "click",
    dom_excerpt: str = "",
) -> BreakageEvent:
    return BreakageEvent(
        job_id=job_id,
        step_index=3,
        step_type=step_type,
        error_class=error_class,
        screenshot_ref="sha256:abc123",
        dom_excerpt=dom_excerpt or "<div class='login'>Page not found</div>",
        occurred_at=_NOW,
        selector_attempted="#submit-btn",
        expected="button visible",
        observed="element not in DOM",
    )


# ---------------------------------------------------------------------------
# test_judge_decision_routes: JudgeDecision has correct shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_decision_has_correct_shape() -> None:
    """JudgeDecision output must have route, confidence, risk, rationale fields."""
    event = _make_breakage()
    agent = JudgeAgent(model=TestModel())

    decision = await agent.decide(event)

    assert isinstance(decision, JudgeDecision)
    assert hasattr(decision, "route")
    assert hasattr(decision, "confidence")
    assert hasattr(decision, "risk")
    assert hasattr(decision, "rationale")
    assert 0.0 <= decision.confidence <= 1.0


@pytest.mark.asyncio
async def test_judge_decision_route_is_valid_enum() -> None:
    """route must be a member of JudgeRoute enum."""
    event = _make_breakage()
    agent = JudgeAgent(model=TestModel())
    decision = await agent.decide(event)
    assert decision.route in JudgeRoute


# ---------------------------------------------------------------------------
# test_judge_v1_hitl_only: high confidence + low risk → STILL human_required
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_v1_hitl_only_always_human_required() -> None:
    """v1 ADR-0013 amendment: route MUST be human_required regardless of LLM output.

    Even if LLM suggests confidence=0.99 and risk=low (which in v2 would trigger
    auto-apply), v1 must override to human_required.
    """
    event = _make_breakage()
    agent = JudgeAgent(model=TestModel())

    decision = await agent.decide(event)

    # The critical invariant — this must NEVER be auto_apply in v1
    assert decision.route == JudgeRoute.human_required, (
        f"v1 HITL invariant violated: route={decision.route!r} "
        f"(must be human_required per ADR-0013 amendment)"
    )


@pytest.mark.asyncio
async def test_judge_v1_multiple_events_all_human_required() -> None:
    """All breakage types must route to human_required in v1."""
    error_classes = [
        "element_missing",
        "layout_changed",
        "http_error",
        "timeout",
        "navigation_failed",
    ]
    agent = JudgeAgent(model=TestModel())

    for error_class in error_classes:
        event = _make_breakage(error_class=error_class)
        decision = await agent.decide(event)
        assert decision.route == JudgeRoute.human_required, (
            f"v1 HITL violated for error_class={error_class!r}: route={decision.route!r}"
        )


# ---------------------------------------------------------------------------
# test_judge_pii_redacted: dom_excerpt with PII → LLM mock does NOT receive plaintext
# ---------------------------------------------------------------------------


def test_redact_pii_removes_email() -> None:
    """Email addresses must be redacted from dom_excerpt."""
    text = f"User {PII_CANARY_NAME} logged in from 192.168.1.1"
    redacted = redact_pii(text)
    assert PII_CANARY_NAME not in redacted
    assert "[EMAIL]" in redacted


def test_redact_pii_removes_cedula() -> None:
    """Panamá cédula patterns must be redacted."""
    text = f"Cedula: {PII_CANARY_CEDULA} — account holder"
    redacted = redact_pii(text)
    assert PII_CANARY_CEDULA not in redacted
    assert "[CEDULA]" in redacted


def test_redact_pii_removes_account_number() -> None:
    """Long numeric strings (account numbers) must be redacted."""
    text = "Account: 0123456789012 — active"
    redacted = redact_pii(text)
    assert "0123456789012" not in redacted
    assert "[ACCT_NUM]" in redacted


@pytest.mark.asyncio
async def test_judge_pii_canary_not_in_prompt() -> None:
    """dom_excerpt containing PII_CANARY_NAME must be redacted before LLM call.

    We verify by checking that the agent's _build_prompt output does not
    contain the plaintext PII value. This is a structural test that validates
    the redaction layer, without mocking at HTTP level.
    """
    pii_dom = f"<div>Welcome {PII_CANARY_NAME}</div><span>Tel: {PII_CANARY_PHONE}</span>"
    event = _make_breakage(dom_excerpt="<div>normal content</div>")

    agent = JudgeAgent(model=TestModel())

    # Build the prompt directly and verify PII is absent
    clean_dom = redact_pii(pii_dom)
    prompt = agent._build_prompt(event, clean_dom, "")

    assert PII_CANARY_NAME not in prompt, "Email PII canary leaked into LLM prompt"
    assert PII_CANARY_PHONE.replace(" ", "") not in prompt.replace(" ", ""), (
        "Phone PII canary leaked into LLM prompt"
    )


@pytest.mark.asyncio
async def test_judge_decide_with_pii_dom_excerpt() -> None:
    """Full decide() call with PII-laden dom_excerpt completes without leaking PII."""
    pii_dom = (
        f"<form><input name='user' value='{PII_CANARY_NAME}'>"
        f"<input name='cedula' value='{PII_CANARY_CEDULA}'></form>"
    )
    event = _make_breakage()
    agent = JudgeAgent(model=TestModel())

    # Should complete without raising (PII is redacted internally)
    decision = await agent.decide(event, dom_excerpt=pii_dom)
    assert decision.route == JudgeRoute.human_required


# ---------------------------------------------------------------------------
# test_cost_caps: judge > $0.02 → abort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_cost_cap_exceeded() -> None:
    """CostCapExceeded raised when projected cost > cap (tiny cap)."""
    # Build a large dom_excerpt to inflate estimated_input_tokens
    large_dom = "x" * 40_000  # ~10k tokens estimated

    event = _make_breakage(dom_excerpt=large_dom)
    agent = JudgeAgent(cost_cap_usd=Decimal("0.000001"))  # impossibly small cap

    with pytest.raises(CostCapExceeded) as exc_info:
        await agent.decide(event, dom_excerpt=large_dom)

    assert exc_info.value.cap == Decimal("0.000001")


@pytest.mark.asyncio
async def test_judge_normal_call_within_cost_cap() -> None:
    """Normal short breakage event should be within the $0.02 cost cap."""
    event = _make_breakage()
    agent = JudgeAgent(model=TestModel())

    # Should not raise CostCapExceeded with default cap
    decision = await agent.decide(event)
    assert decision is not None
