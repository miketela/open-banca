"""JudgeActivity — evaluate breakage events and decide next action via JudgeAgent.

Retry policy (orchestrator.md §Inventario):
  - 1 attempt (judgement is idempotent on the BreakageEvent hash).
  - start-to-close timeout: 30 s.

Idempotency key: SHA-256 hash of the BreakageEvent.

v1 routing (ADR-0013 amendment): ALL breakages → human_required.
confidence/risk populated by JudgeAgent for telemetry only.

Uses DeepSeek V3 (text, no vision per ADR-0006).
In tests, set OPEN_BANCA_TEST_MODEL=1 to inject a PydanticAI TestModel.

NOTE: pydantic_ai imports are deferred to function body to avoid triggering
beartype inside the Temporal workflow sandbox during worker validation.
"""

from __future__ import annotations

import os
from typing import Any

from open_banca_domain.entities.breakage_event import BreakageEvent
from pydantic import BaseModel, Field
from temporalio import activity


class JudgeInput(BaseModel):
    """Input for JudgeActivity."""

    job_id: str = Field(description="Unique job identifier")
    breakage_event: BreakageEvent = Field(description="Breakage event to evaluate")
    breakage_hash: str = Field(
        description="SHA-256 hash of the BreakageEvent for idempotency"
    )
    dom_excerpt: str | None = Field(
        default=None,
        description="Pre-processed DOM excerpt for context (PII redacted in JudgeAgent)",
    )


class JudgeResult(BaseModel):
    """Result from JudgeActivity.

    v1: route is always 'human_required' (ADR-0013 amendment).
    confidence and risk are telemetry fields.
    """

    route: str = Field(
        description="Routing decision — v1 always 'human_required'"
    )
    confidence: float = Field(
        default=0.0,
        description="Agent confidence 0-1 (telemetry only in v1)",
    )
    risk: str = Field(
        default="high",
        description="Risk level: low/med/high (telemetry only in v1)",
    )
    rationale: str = Field(
        default="",
        description="Human-readable reasoning from Judge agent",
    )


class JudgeActivity:
    """JudgeActivity class-based wrapper."""

    def __init__(self, model: Any = None) -> None:
        self._model = model


def _get_judge_model(override: Any) -> Any:
    """Return model to use: explicit override → env flag → None (production default)."""
    if override is not None:
        return override
    if os.environ.get("OPEN_BANCA_TEST_MODEL") == "1":
        from pydantic_ai.models.test import TestModel  # noqa: PLC0415
        return TestModel()
    return None  # JudgeAgent will use its default (deepseek)


@activity.defn(name="JudgeActivity")
async def judge(input: JudgeInput) -> JudgeResult:  # noqa: A002
    """Evaluate a breakage event and decide the recovery path.

    v1 contract (ADR-0013 amendment): always returns route='human_required'.
    The JudgeAgent still invokes the LLM to produce confidence/risk/rationale
    for telemetry and future v2 auto-apply enablement.
    Cost cap: $0.02/call.
    """
    from open_banca_llm.judge.agent import (  # noqa: PLC0415
        CostCapExceeded,
        JudgeAgent,
    )

    model = _get_judge_model(None)
    agent = JudgeAgent(model=model)

    try:
        decision = await agent.decide(
            event=input.breakage_event,
            dom_excerpt=input.dom_excerpt,
        )
    except CostCapExceeded as exc:
        activity.logger.warning("JudgeActivity: cost cap exceeded: %s", exc)
        # On cost cap, still route to human_required (safe default)
        return JudgeResult(
            route="human_required",
            confidence=0.0,
            risk="high",
            rationale=f"Cost cap exceeded: {exc}",
        )

    return JudgeResult(
        route=decision.route.value,
        confidence=decision.confidence,
        risk=decision.risk.value,
        rationale=decision.rationale,
    )
