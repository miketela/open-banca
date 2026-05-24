"""Tests for RemapperAgent — uses FakeChatModel (zero real LLM calls).

Covers:
  - test_remapper_agent.py: FakeChatModel → valid RemapPatch produced
  - test_pii_redact_applied: dom_excerpt with PII_CANARY_NAME → LLM never sees it
  - test_cost_abort: cost > $0.30 → CostExceeded raised
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from open_banca_domain.entities.bank_map import BankMap, StepSpec
from open_banca_domain.entities.breakage_event import BreakageEvent
from open_banca_llm.mapper.agent import FakeChatModel
from open_banca_llm.mapper.errors import CostExceeded
from open_banca_llm.remapper.agent import (
    RemapPatch,
    RemapperAgent,
    RemapperResult,
    RemapRisk,
)

# PII canary used by RedactFilter (configured via env or default canary)
PII_CANARY_NAME = "SECRET_CANARY_VALUE"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def current_map() -> BankMap:
    return BankMap(
        bank_id="banco-general",
        version="1.0.0",
        schema_version="1",
        steps=[
            StepSpec(step_id="s1", action="navigate", target="https://bancogeneral.com"),
            StepSpec(step_id="s2", action="fill", target="#username"),
            StepSpec(step_id="s3", action="click", target="#submit"),
            StepSpec(step_id="s4", action="wait_for_selector", target="#dashboard"),
        ],
    )


@pytest.fixture()
def breakage_event() -> BreakageEvent:
    return BreakageEvent(
        job_id="job-001",
        step_index=2,
        step_type="click",
        error_class="selector_not_found",
        screenshot_ref="sha256:abc123",
        dom_excerpt="<div class='login'><button id='btn-submit'>Login</button></div>",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        selector_attempted="#submit",
    )


def _make_patch_json(
    step_index: int = 2,
    new_steps: list[dict[str, Any]] | None = None,
    confidence: float = 0.85,
    risk: str = "low",
    rationale: str = "Selector changed from #submit to #btn-submit",
) -> str:
    if new_steps is None:
        new_steps = [{"step_id": "s3-fixed", "action": "click", "target": "#btn-submit"}]
    return json.dumps(
        {
            "target_step_index": step_index,
            "new_steps": new_steps,
            "rationale": rationale,
            "confidence": confidence,
            "risk": risk,
        }
    )


# ---------------------------------------------------------------------------
# test_remapper_agent: FakeChatModel → valid RemapPatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_remapper_agent_produces_valid_patch(
    current_map: BankMap, breakage_event: BreakageEvent
) -> None:
    """RemapperAgent with FakeChatModel returns a valid RemapperResult."""
    fake_llm = FakeChatModel(responses=[_make_patch_json()])
    agent = RemapperAgent(llm_override=fake_llm)

    result = await agent.remap(
        breakage_event=breakage_event,
        current_map=current_map,
        sensitive_data={"<USERNAME>": "user", "<PASSWORD>": "pass"},
    )

    assert isinstance(result, RemapperResult)
    assert isinstance(result.patch, RemapPatch)
    assert result.bank_id == "banco-general"
    assert result.patch.target_step_index == 2
    assert len(result.patch.new_steps) == 1
    assert result.patch.new_steps[0].step_id == "s3-fixed"
    assert result.patch.confidence == 0.85
    assert result.patch.risk == RemapRisk.low
    assert result.cost_usd >= 0.0


@pytest.mark.asyncio()
async def test_remapper_returns_remapper_result_type(
    current_map: BankMap, breakage_event: BreakageEvent
) -> None:
    """RemapperResult fields are correctly typed."""
    fake_llm = FakeChatModel(responses=[_make_patch_json()])
    agent = RemapperAgent(llm_override=fake_llm)

    result = await agent.remap(
        breakage_event=breakage_event,
        current_map=current_map,
        sensitive_data={},
    )

    assert isinstance(result.patch.risk, RemapRisk)
    assert 0.0 <= result.patch.confidence <= 1.0
    assert result.patch.rationale


@pytest.mark.asyncio()
async def test_remapper_strips_markdown_fences(
    current_map: BankMap, breakage_event: BreakageEvent
) -> None:
    """RemapperAgent strips markdown code fences from LLM output."""
    raw = "```json\n" + _make_patch_json() + "\n```"
    fake_llm = FakeChatModel(responses=[raw])
    agent = RemapperAgent(llm_override=fake_llm)

    result = await agent.remap(
        breakage_event=breakage_event,
        current_map=current_map,
        sensitive_data={},
    )

    assert isinstance(result.patch, RemapPatch)


@pytest.mark.asyncio()
async def test_remapper_injects_default_target_step_index(
    current_map: BankMap, breakage_event: BreakageEvent
) -> None:
    """If LLM omits target_step_index, RemapperAgent uses breakage_event.step_index."""
    patch_without_index = json.dumps(
        {
            "new_steps": [],
            "rationale": "delete step",
            "confidence": 0.7,
            "risk": "med",
        }
    )
    fake_llm = FakeChatModel(responses=[patch_without_index])
    agent = RemapperAgent(llm_override=fake_llm)

    result = await agent.remap(
        breakage_event=breakage_event,
        current_map=current_map,
        sensitive_data={},
    )

    assert result.patch.target_step_index == breakage_event.step_index


# ---------------------------------------------------------------------------
# test_pii_redact_applied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_pii_redact_applied(
    current_map: BankMap,
) -> None:
    """dom_excerpt containing PII canary → LLM never receives the raw value.

    Injects a RedactConfig with an explicit extra_literal so RedactFilter
    replaces the canary before messages reach FakeChatModel (ADR-0020).
    """
    from open_banca_observability.redact import RedactConfig  # noqa: PLC0415

    canary = "PII_CANARY_NAME_UNIQUE_TOKEN_XYZZY"
    redact_cfg = RedactConfig(extra_literals=[canary])

    dom_with_pii = f"<div>Welcome {canary}</div><button id='submit'>Go</button>"
    event = BreakageEvent(
        job_id="job-pii",
        step_index=1,
        step_type="click",
        error_class="selector_not_found",
        screenshot_ref="sha256:pii",
        dom_excerpt=dom_with_pii,
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    fake_llm = FakeChatModel(responses=[_make_patch_json(step_index=1)])
    agent = RemapperAgent(llm_override=fake_llm, redact_config=redact_cfg)

    await agent.remap(
        breakage_event=event,
        current_map=current_map,
        sensitive_data={},
    )

    # Verify that none of the recorded messages contain the canary
    for msg_list in fake_llm.recorded_messages:
        for msg in msg_list:
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            assert canary not in content, (
                f"PII canary {canary!r} leaked into LLM messages (ADR-0020 violation)"
            )


# ---------------------------------------------------------------------------
# test_cost_abort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_cost_abort(current_map: BankMap, breakage_event: BreakageEvent) -> None:
    """cost > $0.30 → CostExceeded raised before LLM call completes."""
    # Set an extremely low cap so the first call exceeds it

    class _ExpensiveFakeLLM:
        """Fake that always reports 1M tokens to blow the cost cap."""

        _verified_api_keys: bool = False

        @property
        def model(self) -> str:
            return "fake/expensive"

        @property
        def provider(self) -> str:
            return "fake"

        @property
        def name(self) -> str:
            return "expensive"

        async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
            from browser_use.llm.views import ChatInvokeCompletion, ChatInvokeUsage  # noqa: PLC0415

            usage = ChatInvokeUsage(
                prompt_tokens=1_000_000,
                prompt_cached_tokens=None,
                prompt_cache_creation_tokens=None,
                prompt_image_tokens=None,
                completion_tokens=1_000_000,
                total_tokens=2_000_000,
            )
            return ChatInvokeCompletion(
                completion=_make_patch_json(),
                usage=usage,
                stop_reason="end_turn",
            )

    # Cap $0.001 — the expensive LLM will exceed this immediately
    agent = RemapperAgent(llm_override=_ExpensiveFakeLLM(), cost_cap_usd=0.001)

    with pytest.raises(CostExceeded):
        await agent.remap(
            breakage_event=breakage_event,
            current_map=current_map,
            sensitive_data={},
        )
