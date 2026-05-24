"""RemapBankWorkflow — child workflow that fixes a broken bank map.json via the Remapper agent.

Triggered by ScrapeJobWorkflow after Judge emits remap_proposed. Workflow id equals
proposal_id so POST /maps/{bank}/proposals/{id}/approve can signal remap_approved.

Idempotency: bank_id + breakage_hash (orchestrator.md §Inventario).
Timeout: 20 min.

DETERMINISM RULES apply to all code in this module.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from open_banca_domain.entities.bank_map import BankMap
    from open_banca_domain.entities.breakage_event import BreakageEvent
    from open_banca_domain.entities.webhook_event import WebhookEventType
    from open_banca_orchestrator.activities.emit_webhook import (
        EmitWebhookInput,
        emit_webhook,
    )
    from open_banca_orchestrator.activities.persist_remap_map import (
        PersistRemapMapInput,
        persist_remap_map,
    )
    from open_banca_orchestrator.activities.remapper_agent import (
        RemapperAgentInput,
        RemapperAgentResult,
        remapper_agent,
    )

_RETRY_NONE = RetryPolicy(maximum_attempts=1)
_RETRY_PERSIST = RetryPolicy(
    initial_interval=datetime.timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_attempts=2,
)
_RETRY_WEBHOOK = RetryPolicy(
    initial_interval=datetime.timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=datetime.timedelta(hours=1),
    maximum_attempts=5,
)


class RemapBankInput(BaseModel):
    """Input for RemapBankWorkflow."""

    bank_id: str = Field(description="Bank whose map needs repair")
    breakage_hash: str = Field(
        description="SHA-256 hash of the BreakageEvent — idempotency key"
    )
    proposal_id: str = Field(description="Remap proposal ID — also used as workflow id")
    current_map: BankMap = Field(description="Existing (broken) map to repair")
    job_id: str = Field(description="Parent ScrapeJobWorkflow job ID")
    breakage_event: BreakageEvent = Field(description="Breakage that triggered the remap")
    sandbox_container_id: str = Field(description="Sandbox container for visual diff")
    judge_decision: str = Field(default="human_required")


class RemapBankResult(BaseModel):
    """Result from RemapBankWorkflow."""

    updated_map: BankMap | None = Field(default=None)
    status: str = Field(description="completed | rejected | expired | cancelled | failed")
    new_map_version: str | None = None
    proposal_id: str = ""


@workflow.defn(name="RemapBankWorkflow")
class RemapBankWorkflow:
    """Child workflow: repair a broken map.json using the Remapper agent + HITL approval."""

    def __init__(self) -> None:
        self._remap_approved: bool = False
        self._remap_rejected: bool = False
        self._reject_reason: str = ""
        self._cancelled: bool = False

    @workflow.signal(name="remap_approved")
    async def signal_remap_approved(self, _proposal_id: str = "") -> None:
        self._remap_approved = True

    @workflow.signal(name="remap_rejected")
    async def signal_remap_rejected(self, reason: str = "") -> None:
        self._remap_rejected = True
        self._reject_reason = reason

    @workflow.signal(name="cancel_job")
    async def signal_cancel_job(self, _reason: str = "") -> None:
        self._cancelled = True

    @workflow.run
    async def run(self, input: RemapBankInput) -> RemapBankResult:  # noqa: A002
        if self._cancelled:
            return RemapBankResult(
                status="cancelled",
                proposal_id=input.proposal_id,
            )

        remapper_result: RemapperAgentResult = await workflow.execute_activity(
            remapper_agent,
            RemapperAgentInput(
                job_id=input.job_id,
                bank_id=input.bank_id,
                breakage_hash=input.breakage_hash,
                run_id=workflow.uuid4().hex,
                current_map=input.current_map,
                breakage_event=input.breakage_event,
                proposal_id=input.proposal_id,
                judge_decision=input.judge_decision,
                sandbox_container_id=input.sandbox_container_id,
            ),
            start_to_close_timeout=datetime.timedelta(minutes=15),
            retry_policy=_RETRY_NONE,
        )

        await workflow.wait_condition(
            lambda: (
                self._remap_approved
                or self._remap_rejected
                or self._cancelled
            ),
        )

        if self._cancelled:
            return RemapBankResult(
                status="cancelled",
                proposal_id=input.proposal_id,
            )

        if self._remap_rejected:
            await workflow.execute_activity(
                emit_webhook,
                EmitWebhookInput(
                    event_id=workflow.uuid4().hex,
                    event_type=WebhookEventType.JOB_FAILED.value,
                    job_id=input.job_id,
                    payload={
                        "reason": "remap_rejected",
                        "proposal_id": input.proposal_id,
                        "note": self._reject_reason,
                    },
                ),
                start_to_close_timeout=datetime.timedelta(seconds=10),
                retry_policy=_RETRY_WEBHOOK,
            )
            return RemapBankResult(
                status="rejected",
                proposal_id=input.proposal_id,
            )

        persist_result = await workflow.execute_activity(
            persist_remap_map,
            PersistRemapMapInput(
                bank_id=input.bank_id,
                updated_map=remapper_result.updated_map,
                proposal_id=input.proposal_id,
            ),
            start_to_close_timeout=datetime.timedelta(seconds=30),
            retry_policy=_RETRY_PERSIST,
        )

        await workflow.execute_activity(
            emit_webhook,
            EmitWebhookInput(
                event_id=workflow.uuid4().hex,
                event_type=WebhookEventType.JOB_PROGRESS.value,
                job_id=input.job_id,
                payload={
                    "phase": "remap_applied_after_review",
                    "proposal_id": input.proposal_id,
                    "new_map_version": persist_result.version,
                    "persisted": persist_result.persisted,
                },
            ),
            start_to_close_timeout=datetime.timedelta(seconds=10),
            retry_policy=_RETRY_WEBHOOK,
        )

        return RemapBankResult(
            updated_map=remapper_result.updated_map,
            status="completed",
            new_map_version=persist_result.version,
            proposal_id=input.proposal_id,
        )
