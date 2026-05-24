"""Tests for RemapBankWorkflow — HITL self-healing child workflow."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from open_banca_domain.entities.bank_map import BankMap, StepSpec
from open_banca_domain.entities.breakage_event import BreakageEvent
from open_banca_orchestrator.activities.emit_webhook import EmitWebhookResult
from open_banca_orchestrator.activities.persist_remap_map import PersistRemapMapResult
from open_banca_orchestrator.activities.remapper_agent import RemapperAgentResult
from open_banca_orchestrator.workflows.remap_bank import (
    RemapBankInput,
    RemapBankResult,
    RemapBankWorkflow,
)

_UPDATED_MAP = BankMap(
    bank_id="banco_general",
    version="1.0.1",
    schema_version="1",
    steps=[StepSpec(step_id="s1", action="click", target="#fixed")],
)

_BREAKAGE = BreakageEvent(
    job_id="job-remap-001",
    step_index=1,
    step_type="click",
    error_class="selector_not_found",
    screenshot_ref="sha256:abc",
    dom_excerpt="<div/>",
    occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
)

_CURRENT_MAP = BankMap(
    bank_id="banco_general",
    version="1.0.0",
    schema_version="1",
    steps=[StepSpec(step_id="s1", action="click", target="#broken")],
)


@activity.defn(name="RemapperAgentActivity")
async def _mock_remapper(raw_input):  # type: ignore[no-untyped-def]
    from open_banca_orchestrator.activities.remapper_agent import RemapperAgentInput

    inp = (
        RemapperAgentInput.model_validate(raw_input)
        if isinstance(raw_input, dict)
        else raw_input
    )
    return RemapperAgentResult(
        updated_map=_UPDATED_MAP,
        llm_cost_usd=0.05,
        diff_summary="fixed selector",
        proposal_id=inp.proposal_id,
        applied=False,
    )


@activity.defn(name="PersistRemapMapActivity")
async def _mock_persist_remap(raw_input):  # type: ignore[no-untyped-def]
    from open_banca_orchestrator.activities.persist_remap_map import PersistRemapMapInput

    inp = (
        PersistRemapMapInput.model_validate(raw_input)
        if isinstance(raw_input, dict)
        else raw_input
    )
    return PersistRemapMapResult(
        map_path=f"/tmp/{inp.bank_id}/map.json",
        version=inp.updated_map.version,
        persisted=True,
    )


@activity.defn(name="EmitWebhookActivity")
async def _mock_emit(_input):  # type: ignore[no-untyped-def]
    return EmitWebhookResult(enqueued=True, event_id="evt-remap-001")


_REMAP_ACTIVITIES = [_mock_remapper, _mock_persist_remap, _mock_emit]


def _remap_input(proposal_id: str = "proposal-remap-001") -> RemapBankInput:
    return RemapBankInput(
        bank_id="banco_general",
        breakage_hash="sha256:break",
        proposal_id=proposal_id,
        current_map=_CURRENT_MAP,
        job_id="job-remap-001",
        breakage_event=_BREAKAGE,
        sandbox_container_id="sandbox-001",
    )


@pytest.fixture
def thread_pool() -> ThreadPoolExecutor:
    with ThreadPoolExecutor(max_workers=2) as pool:
        yield pool


@pytest.mark.asyncio
async def test_remap_bank_workflow_completes_after_approval(
    thread_pool: ThreadPoolExecutor,
) -> None:
    proposal_id = "proposal-remap-001"
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue="remap-queue",
            workflows=[RemapBankWorkflow],
            activities=_REMAP_ACTIVITIES,
            activity_executor=thread_pool,
        ):
            handle = await env.client.start_workflow(
                RemapBankWorkflow.run,
                _remap_input(proposal_id),
                id=proposal_id,
                task_queue="remap-queue",
            )
            await handle.signal(RemapBankWorkflow.signal_remap_approved, proposal_id)
            result: RemapBankResult = await handle.result()

    assert result.status == "completed"
    assert result.new_map_version == "1.0.1"
    assert result.updated_map is not None
    assert result.updated_map.steps[0].target == "#fixed"


@pytest.mark.asyncio
async def test_remap_bank_workflow_rejected(
    thread_pool: ThreadPoolExecutor,
) -> None:
    proposal_id = "proposal-remap-reject"
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue="remap-queue",
            workflows=[RemapBankWorkflow],
            activities=_REMAP_ACTIVITIES,
            activity_executor=thread_pool,
        ):
            handle = await env.client.start_workflow(
                RemapBankWorkflow.run,
                _remap_input(proposal_id),
                id=proposal_id,
                task_queue="remap-queue",
            )
            await handle.signal(
                RemapBankWorkflow.signal_remap_rejected,
                "operator rejected",
            )
            result: RemapBankResult = await handle.result()

    assert result.status == "rejected"
