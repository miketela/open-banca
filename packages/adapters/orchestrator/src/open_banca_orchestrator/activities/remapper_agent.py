"""RemapperAgentActivity — run the Remapper AI agent to fix a broken bank map.json.

PLACEHOLDER — wired in task 20 (RemapBankWorkflow + RemapperAgent).

Retry policy (orchestrator.md §Inventario):
  - NO retry (expensive LLM call — sin retry).
  - start-to-close timeout: 15 min.
  - Heartbeat every 30 s.

Idempotency key: bank_id + breakage_hash + run_id.

Uses Claude Sonnet 4.6 vision to diff the current map.json against the broken
state and produce a corrected map.json.

Cost guardrail: aborts with ApplicationError(non_retryable=True) if $0.50/job
LLM budget is exceeded.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from temporalio import activity

from open_banca_orchestrator.activities.mapper_agent import BankMap


class RemapperAgentInput(BaseModel):
    """Input for RemapperAgentActivity."""

    job_id: str = Field(description="Parent job identifier")
    bank_id: str = Field(description="Bank whose map needs repair")
    breakage_hash: str = Field(
        description="SHA-256 hash of the BreakageEvent that triggered the remap"
    )
    run_id: str = Field(description="Unique run ID for idempotency")
    current_map: BankMap = Field(description="Existing (broken) map.json to repair")
    proposal_id: str = Field(
        description="Proposal ID from JudgeActivity — included in webhook event"
    )
    sandbox_container_id: str = Field(
        description="Sandbox container with live browser for visual diff"
    )
    llm_budget_usd: float = Field(
        default=0.50,
        description="Maximum LLM spend allowed for this remapping run",
    )


class RemapperAgentResult(BaseModel):
    """Result from RemapperAgentActivity."""

    updated_map: BankMap = Field(description="Repaired declarative navigation map")
    llm_cost_usd: float = Field(description="Actual LLM spend for this run")
    diff_summary: str = Field(description="Human-readable summary of changes made")


class RemapperAgentActivity:
    """RemapperAgentActivity class-based wrapper."""


@activity.defn(name="RemapperAgentActivity")
async def remapper_agent(input: RemapperAgentInput) -> RemapperAgentResult:  # noqa: A002
    """Run the Remapper agent to fix a broken map.json.

    PLACEHOLDER — wired in task 20 (RemapBankWorkflow + RemapperAgent).

    Sends heartbeats every 30 s.

    TODO: integrate with PydanticAI + Claude Sonnet 4.6 vision (task 20).
    TODO: enforce LLM cost budget guardrail (abort if budget exceeded).
    TODO: call activity.heartbeat(step=step_name) every 30 s.
    TODO: persist updated map.json (cosign-signed).
    TODO: emit remap_completed webhook event.
    """
    raise NotImplementedError(
        "RemapperAgentActivity not implemented — PLACEHOLDER, wired in task 20"
    )
