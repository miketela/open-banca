"""RemapBankWorkflow — child workflow that fixes a broken bank map.json via the Remapper agent.

SKELETON — full implementation in task 20 (RemapBankWorkflow + RemapperAgent).

Triggered by ScrapeJobWorkflow after Judge emits remap_proposed and the
remap_approved signal is received.

Idempotency: bank_id + breakage_hash (orchestrator.md §Inventario).
Timeout: 20 min.

DETERMINISM RULES apply to all code in this module.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from open_banca_orchestrator.activities.mapper_agent import BankMap


class RemapBankInput(BaseModel):
    """Input for RemapBankWorkflow."""

    bank_id: str = Field(description="Bank whose map needs repair")
    breakage_hash: str = Field(description="SHA-256 hash of the BreakageEvent — idempotency key")
    proposal_id: str = Field(description="Remap proposal ID from JudgeActivity")
    current_map: BankMap = Field(description="Existing (broken) map to repair")
    job_id: str = Field(description="Parent ScrapeJobWorkflow job ID")


class RemapBankResult(BaseModel):
    """Result from RemapBankWorkflow."""

    updated_map: BankMap | None = Field(
        default=None,
        description="Repaired map — None if workflow was not yet implemented",
    )
    status: str = Field(default="not_implemented")


@workflow.defn(name="RemapBankWorkflow")
class RemapBankWorkflow:
    """Child workflow: repair a broken map.json using the Remapper agent.

    SKELETON — wired in task 20.
    """

    @workflow.run
    async def run(self, input: RemapBankInput) -> RemapBankResult:
        """Run the RemapBank workflow.

        SKELETON — raises NotImplementedError.
        Full implementation in task 20 (RemapBankWorkflow + RemapperAgent).

        TODO: spawn sandbox container for visual diff.
        TODO: execute_activity(remapper_agent, ...) with no-retry policy.
        TODO: persist updated map.json (cosign-signed).
        TODO: emit remap_completed webhook event via EmitWebhookActivity.
        """
        raise NotImplementedError("RemapBankWorkflow not implemented — SKELETON, wired in task 20")
