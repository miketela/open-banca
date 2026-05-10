"""ApplyRemapProposal use case — approve or reject a remap proposal."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from open_banca_domain.entities.remap_proposal import RemapStatus
from open_banca_domain.ports.event_bus_port import EventBusPort
from open_banca_domain.ports.job_store_port import JobStorePort
from open_banca_domain.ports.orchestrator_port import OrchestratorPort
from pydantic import BaseModel


class RemapAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApplyRemapInput(BaseModel):
    proposal_id: str
    action: RemapAction


@dataclass
class ApplyRemapOutput:
    success: bool
    new_status: str


class ApplyRemapProposal:
    """Updates proposal status and signals the orchestrator when approved."""

    def __init__(
        self,
        job_store: JobStorePort,
        orchestrator: OrchestratorPort,
        event_bus: EventBusPort,
    ) -> None:
        self._job_store = job_store
        self._orchestrator = orchestrator
        self._event_bus = event_bus

    def execute(self, input: ApplyRemapInput) -> ApplyRemapOutput:
        proposal = self._job_store.load_proposal(input.proposal_id)  # type: ignore[attr-defined]
        if proposal is None:
            raise ValueError(f"Proposal not found: {input.proposal_id}")

        new_status = (
            RemapStatus.APPROVED if input.action == RemapAction.APPROVE else RemapStatus.REJECTED
        )

        if input.action == RemapAction.APPROVE:
            self._orchestrator.signal_remap_approved(input.proposal_id)

        return ApplyRemapOutput(success=True, new_status=new_status.value)
