"""MapBankWorkflow — child workflow that generates a bank's map.json via the Mapper agent.

SKELETON — full implementation in task 14 (MapBankWorkflow + MapperAgent).

Triggered by ScrapeJobWorkflow when no map.json exists for the requested bank.
Runs MapperAgentActivity with no retry (expensive LLM) and persists the result.

Idempotency: bank_id + map_version_target (orchestrator.md §Inventario).
Timeout: 30 min.

DETERMINISM RULES apply to all code in this module.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from open_banca_orchestrator.activities.mapper_agent import BankMap


class MapBankInput(BaseModel):
    """Input for MapBankWorkflow."""

    bank_id: str = Field(description="Bank to map")
    map_version_target: str = Field(
        default="1.0.0",
        description="Target map version for idempotency key",
    )
    job_id: str = Field(description="Parent ScrapeJobWorkflow job ID")


class MapBankResult(BaseModel):
    """Result from MapBankWorkflow."""

    bank_map: BankMap | None = Field(
        default=None,
        description="Generated bank map — None if workflow was not yet implemented",
    )
    status: str = Field(default="not_implemented")


@workflow.defn(name="MapBankWorkflow")
class MapBankWorkflow:
    """Child workflow: generate map.json for a bank using the Mapper agent.

    SKELETON — wired in task 14.
    """

    @workflow.run
    async def run(self, input: MapBankInput) -> MapBankResult:
        """Run the MapBank workflow.

        SKELETON — raises NotImplementedError.
        Full implementation in task 14 (MapBankWorkflow + MapperAgent).

        TODO: spawn sandbox container for visual exploration.
        TODO: execute_activity(mapper_agent, ...) with no-retry policy.
        TODO: persist generated map.json to storage (cosign-signed).
        TODO: emit map_generated webhook event.
        """
        raise NotImplementedError("MapBankWorkflow not implemented — SKELETON, wired in task 14")
