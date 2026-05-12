"""JudgeAgent — evaluate BreakageEvent and produce routing decision.

Architecture (ADR-0006, ADR-0013 amendment):
  - Text-only DeepSeek V3 (NO vision — vision deferred per ADR-0006).
  - v1 routing: route is ALWAYS human_required regardless of confidence/risk.
    confidence and risk are computed and stored for telemetry only.
  - PII redaction applied to dom_excerpt before LLM invocation (ADR-0020).
  - Cost cap: $0.02/call.

Input: BreakageEvent + optional dom_excerpt (pre-processed, no raw screenshots).
Output: JudgeDecision with route, confidence, risk, rationale.
"""

from __future__ import annotations

import re
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from open_banca_domain.entities.breakage_event import BreakageEvent
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

# ---------------------------------------------------------------------------
# Cost helpers (reuse same DeepSeek pricing as validator)
# ---------------------------------------------------------------------------

_DEEPSEEK_INPUT_COST_PER_M = Decimal("0.27")
_DEEPSEEK_OUTPUT_COST_PER_M = Decimal("1.10")

JUDGE_COST_CAP_USD = Decimal("0.02")


class CostCapExceeded(Exception):
    """Raised when LLM cost exceeds the judge per-call cap."""

    def __init__(self, cost: Decimal, cap: Decimal) -> None:
        super().__init__(f"Judge cost cap exceeded: ${cost:.4f} > ${cap:.4f}")
        self.cost = cost
        self.cap = cap


def _compute_cost(input_tokens: int, output_tokens: int) -> Decimal:
    return (
        Decimal(input_tokens) / Decimal(1_000_000) * _DEEPSEEK_INPUT_COST_PER_M
        + Decimal(output_tokens) / Decimal(1_000_000) * _DEEPSEEK_OUTPUT_COST_PER_M
    )


# ---------------------------------------------------------------------------
# PII redaction
# ---------------------------------------------------------------------------

# Common PII patterns to redact from dom_excerpt before sending to LLM.
# This is a conservative list; real production hardening uses a separate
# PII-detection service. Redact: emails, phone numbers, Panamá cedula-like
# patterns (X-XXX-XXXX), credit card-like numbers, and any 16-digit numbers.
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Email addresses — must come first to avoid partial matches by other patterns
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.I), "[EMAIL]"),
    # Panamá cédula: X-XXX-XXXX or X-XXXX-XXXXX (specific, before phone/account)
    (re.compile(r"\b\d{1,2}-\d{3,4}-\d{4,6}\b"), "[CEDULA]"),
    # Generic long numeric strings (account numbers, 10+ digits)
    (re.compile(r"\b\d{10,}\b"), "[ACCT_NUM]"),
    # Phone numbers: must have + or ( prefix or be >= 10 digits with separators
    # Pattern: starts with + or (, followed by digits/spaces/dashes
    (re.compile(r"(?:\+|(?<!\w)\()\d[\d\s\-().]{6,14}\d"), "[PHONE]"),
]


def redact_pii(text: str) -> str:
    """Apply all PII patterns to text and return redacted version."""
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# I/O types
# ---------------------------------------------------------------------------


class JudgeRoute(StrEnum):
    """Routing decision from the Judge agent.

    v1 contract: route is ALWAYS human_required (ADR-0013 amendment).
    Other values are defined for v2 auto-apply path (not active in v1).
    """

    retry = "retry"
    partial_remap = "partial_remap"
    full_remap = "full_remap"
    abort = "abort"
    human_required = "human_required"


class JudgeRisk(StrEnum):
    """Risk level assigned by the Judge (telemetry only in v1)."""

    low = "low"
    med = "med"
    high = "high"


class JudgeDecision(BaseModel):
    """Structured output from JudgeAgent.

    In v1: route is always human_required (ADR-0013).
    confidence and risk are for telemetry/dashboards only.
    """

    route: JudgeRoute = Field(
        description="Routing decision — v1 always human_required"
    )
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        description="Agent confidence 0-1 (telemetry only in v1)"
    )
    risk: JudgeRisk = Field(
        description="Risk assessment (telemetry only in v1)"
    )
    rationale: str = Field(
        description="Human-readable explanation of the decision"
    )


# LLM output schema — what DeepSeek returns (before v1 override)
class _LLMJudgeOutput(BaseModel):
    """Raw structured output from DeepSeek (pre-v1-override)."""

    suggested_route: JudgeRoute = Field(
        description="Suggested route (overridden to human_required in v1)"
    )
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    risk: JudgeRisk
    rationale: str


# ---------------------------------------------------------------------------
# JudgeAgent
# ---------------------------------------------------------------------------


class JudgeAgent:
    """Judge agent: evaluate BreakageEvent and produce JudgeDecision.

    Args:
        model: PydanticAI model. Production: ``deepseek:deepseek-chat``.
               Tests: inject ``TestModel`` or ``FunctionModel``.
        cost_cap_usd: Per-call cost cap (default $0.02).
    """

    def __init__(
        self,
        model: Model | str | None = None,
        cost_cap_usd: Decimal = JUDGE_COST_CAP_USD,
    ) -> None:
        self._model: Model | str | None = model
        self._cost_cap = cost_cap_usd
        # Agent lazily constructed on first call to avoid requiring DEEPSEEK_API_KEY
        # when the JudgeAgent is instantiated in tests without a model override.
        self._agent: Agent[None, _LLMJudgeOutput] | None = None

    def _get_agent(self) -> Agent[None, _LLMJudgeOutput]:
        """Lazily create (or return cached) the PydanticAI Agent."""
        if self._agent is None:
            model: Model | str = self._model or "deepseek:deepseek-chat"
            self._agent = Agent(
                model,
                output_type=_LLMJudgeOutput,
                system_prompt=(
                    "You are a bank scrape breakage analyst. "
                    "Given a description of a scrape failure (error class, step type, "
                    "dom excerpt, and context), classify the breakage and suggest a "
                    "recovery route from: retry, partial_remap, full_remap, abort, "
                    "or human_required. "
                    "Provide a confidence score (0-1), risk level (low/med/high), "
                    "and a concise rationale. "
                    "DO NOT include any PII in your response."
                ),
            )
        return self._agent

    async def decide(
        self,
        event: BreakageEvent,
        dom_excerpt: str | None = None,
        page_summary: str | None = None,
    ) -> JudgeDecision:
        """Evaluate a BreakageEvent and return a JudgeDecision.

        v1 contract (ADR-0013 amendment): route is ALWAYS human_required.
        The LLM is still invoked to populate confidence/risk/rationale for
        telemetry, but the returned route field is unconditionally overridden.

        Args:
            event: The BreakageEvent to evaluate.
            dom_excerpt: Pre-processed HTML excerpt (PII will be redacted).
            page_summary: Optional OCR/text summary of the page (no raw images).

        Returns:
            JudgeDecision with route=human_required (v1), plus telemetry fields.

        Raises:
            CostCapExceeded: If LLM cost exceeds the configured cap.
        """
        # Redact PII from dom_excerpt before building prompt
        clean_dom = redact_pii(dom_excerpt) if dom_excerpt else ""
        clean_summary = redact_pii(page_summary) if page_summary else ""

        prompt = self._build_prompt(event, clean_dom, clean_summary)

        # Pre-estimate cost
        estimated_input_tokens = len(prompt) // 4
        estimated_output_tokens = 150
        projected_cost = _compute_cost(estimated_input_tokens, estimated_output_tokens)

        if projected_cost > self._cost_cap:
            raise CostCapExceeded(projected_cost, self._cost_cap)

        result = await self._get_agent().run(prompt)

        usage = result.usage()
        actual_cost = _compute_cost(
            usage.input_tokens or estimated_input_tokens,
            usage.output_tokens or estimated_output_tokens,
        )

        if actual_cost > self._cost_cap:
            raise CostCapExceeded(actual_cost, self._cost_cap)

        llm_output = result.output

        # v1 ADR-0013 amendment: unconditionally override route to human_required.
        # confidence/risk/rationale preserved for telemetry.
        return JudgeDecision(
            route=JudgeRoute.human_required,
            confidence=llm_output.confidence,
            risk=llm_output.risk,
            rationale=llm_output.rationale,
        )

    def _build_prompt(
        self,
        event: BreakageEvent,
        dom_excerpt: str,
        page_summary: str,
    ) -> str:
        """Build text prompt for DeepSeek (no vision, ADR-0006)."""
        lines: list[str] = [
            f"job_id: {event.job_id}",
            f"step_index: {event.step_index}",
            f"step_type: {event.step_type}",
            f"error_class: {event.error_class}",
            f"screenshot_ref: {event.screenshot_ref}",
        ]
        if event.http_status is not None:
            lines.append(f"http_status: {event.http_status}")
        if event.selector_attempted:
            lines.append(f"selector_attempted: {event.selector_attempted}")
        if event.expected:
            lines.append(f"expected: {event.expected}")
        if event.observed:
            lines.append(f"observed: {event.observed}")

        if dom_excerpt:
            # Truncate to 2000 chars to stay within token budget
            truncated = dom_excerpt[:2000]
            lines.append(f"dom_excerpt (redacted, truncated):\n{truncated}")

        if page_summary:
            lines.append(f"page_summary: {page_summary[:500]}")

        return "\n".join(lines)

    def override_model(self, model: Model | str) -> JudgeAgent:
        """Return a new JudgeAgent with the model overridden (for testing)."""
        return JudgeAgent(model=model, cost_cap_usd=self._cost_cap)
