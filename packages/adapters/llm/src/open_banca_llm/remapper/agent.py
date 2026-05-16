"""RemapperAgent — browser-use + Claude Sonnet 4.6 vision for partial map repair.

Architecture:
  1. Reuses MapperAgent's CostTrackingChatModel / PIIRedactingChatModel chain.
  2. Operates on a focused window: step_index-1 .. step_index+2.
  3. Output: RemapPatch with new_steps, rationale, confidence, risk enum.
  4. Cost cap $0.30 / wallclock 3 min (cheaper than full mapper).
  5. PII redaction mandatory before any LLM call (ADR-0020).

REQ-003-amendment (Remapper), REQ-011 (cost cap), ADR-0013 (HITL-only v1), ADR-0020.
"""

from __future__ import annotations

import json
import logging
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from open_banca_domain.entities.bank_map import BankMap, StepSpec
from open_banca_domain.entities.breakage_event import BreakageEvent
from open_banca_llm.mapper.agent import CostTrackingChatModel, FakeChatModel  # noqa: F401
from open_banca_llm.mapper.cost_tracker import CostTracker
from open_banca_llm.mapper.errors import CostExceeded, MapperError, WallclockExceeded
from open_banca_llm.mapper.pii_filter_adapter import PIIRedactingChatModel, PiiRegion
from open_banca_observability.redact import RedactConfig

logger = logging.getLogger(__name__)

# Cost / time caps for remapper (cheaper than full mapper)
REMAPPER_COST_CAP_USD: float = 0.30
REMAPPER_WALLCLOCK_SECONDS: float = 180.0  # 3 min

_DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"

# How many steps before / after the failing index to re-explore
_CONTEXT_BEFORE = 1
_CONTEXT_AFTER = 2


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


class RemapRisk(StrEnum):
    """Risk level for a proposed remap patch."""

    low = "low"
    med = "med"
    high = "high"


class RemapPatch(BaseModel):
    """LLM-generated patch for a specific window of broken steps.

    Fields
    ------
    target_step_index : Zero-based index of the step to replace/insert from.
    new_steps         : Replacement steps for the window (may be empty to delete).
    rationale         : Human-readable explanation of the change.
    confidence        : Agent confidence 0-1.
    risk              : Risk level (low / med / high).
    """

    target_step_index: int = Field(ge=0)
    new_steps: list[StepSpec] = Field(default_factory=list)
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    risk: RemapRisk


class RemapperResult(BaseModel):
    """Full output from RemapperAgent.remap()."""

    patch: RemapPatch
    cost_usd: float
    bank_id: str


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are an expert bank scraping engineer. "
    "A Playwright automation step has broken due to a website layout change. "
    "Analyse the DOM excerpt and context provided, then output a JSON patch "
    "describing the corrected navigation steps for the affected window ONLY. "
    "Return ONLY valid JSON — no markdown fences, no prose. "
    'Schema: {"target_step_index": int, "new_steps": [...], '
    '"rationale": "...", "confidence": 0.0-1.0, "risk": "low|med|high"}'
)


def _build_remap_prompt(
    breakage: BreakageEvent,
    current_map: BankMap,
    window_steps: list[StepSpec],
    redacted_dom: str,
) -> str:
    """Build the user prompt for the remapper LLM call."""
    lines = [
        f"bank_id: {current_map.bank_id}",
        f"map_version: {current_map.version}",
        f"failing_step_index: {breakage.step_index}",
        f"step_type: {breakage.step_type}",
        f"error_class: {breakage.error_class}",
        f"screenshot_ref: {breakage.screenshot_ref}",
    ]
    if breakage.selector_attempted:
        lines.append(f"selector_attempted: {breakage.selector_attempted}")
    if breakage.expected:
        lines.append(f"expected: {breakage.expected}")
    if breakage.observed:
        lines.append(f"observed: {breakage.observed}")

    lines.append(
        f"window_steps (indices {max(0, breakage.step_index - _CONTEXT_BEFORE)}"
        f"..{breakage.step_index + _CONTEXT_AFTER}):"
    )
    for s in window_steps:
        lines.append(f"  {json.dumps(s.model_dump())}")

    if redacted_dom:
        lines.append(f"dom_excerpt (redacted):\n{redacted_dom[:3000]}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PII redaction helper (reuses JudgeAgent pattern + RedactFilter)
# ---------------------------------------------------------------------------


def _redact_dom(dom: str, redact_config: RedactConfig | None = None) -> str:
    """Apply RedactFilter to a DOM excerpt before sending to LLM."""
    from open_banca_observability.redact import RedactFilter

    f = RedactFilter(config=redact_config)
    return f.scrub(dom)


# ---------------------------------------------------------------------------
# RemapperAgent
# ---------------------------------------------------------------------------


class RemapperAgent:
    """Generates a RemapPatch by invoking Claude Sonnet 4.6 vision on a narrow step window.

    Args:
        model: LiteLLM model string (default: anthropic/claude-sonnet-4-6).
        cost_cap_usd: Abort if cost exceeds this (default $0.30).
        wallclock_cap_seconds: Abort if elapsed time exceeds this (default 3 min).
        pii_regions: Pixel regions to black-out in screenshots (ADR-0020 Capa 2).
        redact_config: RedactFilter config for DOM/text scrubbing.
        llm_override: Inject a custom BaseChatModel (tests pass FakeChatModel here).
    """

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        cost_cap_usd: float = REMAPPER_COST_CAP_USD,
        wallclock_cap_seconds: float = REMAPPER_WALLCLOCK_SECONDS,
        pii_regions: list[PiiRegion] | None = None,
        redact_config: RedactConfig | None = None,
        llm_override: Any | None = None,
    ) -> None:
        self._model_name = model
        self._cost_cap = cost_cap_usd
        self._wallclock_cap = wallclock_cap_seconds
        self._pii_regions = pii_regions or []
        self._redact_config = redact_config
        self._llm_override = llm_override

    def _build_llm(self, tracker: CostTracker) -> CostTrackingChatModel:
        """Build LLM chain: (LiteLLM | override) → PIIRedact → CostTracking."""
        if self._llm_override is not None:
            inner: Any = self._llm_override
        else:
            from browser_use.llm.litellm.chat import (
                ChatLiteLLM,  # type: ignore[import-untyped]
            )

            inner = ChatLiteLLM(model=self._model_name)

        pii_wrapped = PIIRedactingChatModel(
            inner=inner,
            redact_config=self._redact_config,
            pii_regions=self._pii_regions,
        )

        return CostTrackingChatModel(
            inner=pii_wrapped,
            tracker=tracker,
            agent_name="remapper",
        )

    def _get_window(self, current_map: BankMap, step_index: int) -> list[StepSpec]:
        """Extract the step window around the failing index."""
        start = max(0, step_index - _CONTEXT_BEFORE)
        end = min(len(current_map.steps), step_index + _CONTEXT_AFTER + 1)
        return list(current_map.steps[start:end])

    async def remap(
        self,
        breakage_event: BreakageEvent,
        current_map: BankMap,
        sensitive_data: dict[str, str],
    ) -> RemapperResult:
        """Re-explore the broken step window and return a RemapPatch.

        Args:
            breakage_event: BreakageEvent describing the failing step.
            current_map: Current BankMap (with the broken step).
            sensitive_data: Credential placeholders — never passed to LLM.

        Returns:
            RemapperResult with patch, cost, and bank_id.

        Raises:
            CostExceeded: If LLM cost exceeds $0.30 cap.
            WallclockExceeded: If elapsed time exceeds 3 min.
            MapperError: Other failures.
        """
        tracker = CostTracker(
            cost_cap_usd=self._cost_cap,
            wallclock_cap_seconds=self._wallclock_cap,
        )
        llm = self._build_llm(tracker)

        # PII-redact DOM before LLM (ADR-0020)
        redacted_dom = _redact_dom(breakage_event.dom_excerpt, self._redact_config)

        window = self._get_window(current_map, breakage_event.step_index)
        prompt = _build_remap_prompt(breakage_event, current_map, window, redacted_dom)

        logger.info(
            "RemapperAgent starting: bank_id=%s, step_index=%d, cost_cap=$%.2f",
            current_map.bank_id,
            breakage_event.step_index,
            self._cost_cap,
        )

        raw_json = await self._invoke_llm(llm, prompt)
        patch = self._parse_patch(raw_json, breakage_event.step_index)

        cost = tracker.usage.cost_usd
        logger.info(
            "RemapperAgent complete: bank_id=%s, new_steps=%d, confidence=%.2f, cost=$%.4f",
            current_map.bank_id,
            len(patch.new_steps),
            patch.confidence,
            cost,
        )

        return RemapperResult(
            patch=patch,
            cost_usd=cost,
            bank_id=current_map.bank_id,
        )

    async def _invoke_llm(self, llm: CostTrackingChatModel, prompt: str) -> str:
        """Call the LLM with the remap prompt; return raw string output."""
        from browser_use.llm.messages import (  # type: ignore[import-untyped]
            SystemMessage,
            UserMessage,
        )

        messages = [
            SystemMessage(role="system", content=_SYSTEM_PROMPT),
            UserMessage(role="user", content=prompt),
        ]
        try:
            result = await llm.ainvoke(messages)
        except (CostExceeded, WallclockExceeded):
            raise
        except Exception as exc:
            raise MapperError(f"RemapperAgent LLM call failed: {exc}") from exc

        return str(result.completion)

    def _parse_patch(self, raw: str, step_index: int) -> RemapPatch:
        """Parse LLM JSON output into a RemapPatch."""
        text = raw.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(line for line in lines if not line.startswith("```")).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MapperError(f"RemapperAgent output is not valid JSON: {exc}") from exc

        # Provide defaults for optional fields
        data.setdefault("target_step_index", step_index)
        data.setdefault("new_steps", [])
        data.setdefault("confidence", 0.5)
        data.setdefault("risk", "high")

        try:
            return RemapPatch.model_validate(data)
        except Exception as exc:
            raise MapperError(f"RemapPatch schema validation failed: {exc}") from exc
