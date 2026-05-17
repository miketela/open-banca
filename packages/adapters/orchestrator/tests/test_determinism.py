"""Determinism tests — verify ScrapeJobWorkflow replay is non-deterministic-error-free."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from open_banca_orchestrator.activities.emit_webhook import EmitWebhookResult
from open_banca_orchestrator.activities.execute_scrape_map import ExecuteScrapeMapResult
from open_banca_orchestrator.activities.parse_excel import ParseExcelResult
from open_banca_orchestrator.activities.validate import ValidateResult, ValidationStatus
from open_banca_orchestrator.activities.spawn_sandbox import SpawnSandboxResult
from open_banca_orchestrator.activities.cleanup_sandbox import CleanupSandboxResult
from open_banca_orchestrator.activities.persist_result import PersistResultResult
from open_banca_orchestrator.workflows.map_bank import MapBankWorkflow
from open_banca_orchestrator.workflows.remap_bank import RemapBankWorkflow
from open_banca_orchestrator.workflows.scrape_job import (
    ScrapeJobInput,
    ScrapeJobWorkflow,
    ScrapeMode,
)


@activity.defn(name="ExecuteScrapeMapActivity")
async def _mock_execute_scrape_map(_input):  # type: ignore[no-untyped-def]
    return ExecuteScrapeMapResult(
        status="completed",
        excel_path="/tmp/test.xlsx",
        file_size_bytes=512,
        content_hash="abc",
        steps_completed=1,
    )


@activity.defn(name="ParseExcelActivity")
def _mock_parse_excel(_input):  # type: ignore[no-untyped-def]
    return ParseExcelResult(transactions=[], row_count=0)


@activity.defn(name="ValidateActivity")
async def _mock_validate(_input):  # type: ignore[no-untyped-def]
    return ValidateResult(status=ValidationStatus.ok, validated_count=0)


@activity.defn(name="EmitWebhookActivity")
async def _mock_emit(_input):  # type: ignore[no-untyped-def]
    return EmitWebhookResult(enqueued=True, event_id="evt-det-001")


@activity.defn(name="SpawnSandboxActivity")
async def _mock_spawn_sandbox(_input):  # type: ignore[no-untyped-def]
    return SpawnSandboxResult(
        container_id="test-sandbox", sidecar_socket_path="/run/banca/sidecar.sock"
    )


@activity.defn(name="CleanupSandboxActivity")
async def _mock_cleanup_sandbox(_input):  # type: ignore[no-untyped-def]
    return CleanupSandboxResult(cleaned=True)


@activity.defn(name="PersistResultActivity")
async def _mock_persist_result(_input):  # type: ignore[no-untyped-def]
    return PersistResultResult(persisted_accounts=1, persisted_transactions=0)


@pytest.mark.asyncio
async def test_scrape_job_workflow_replay_is_deterministic() -> None:
    input_ = ScrapeJobInput(
        job_id="replay-test-001",
        bank_id="banco_general",
        credential_ref="cred-ref",
        mode=ScrapeMode.full_historical,
        account_filter=["acc-001"],
    )

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue="replay-test-queue",
            workflows=[ScrapeJobWorkflow, MapBankWorkflow, RemapBankWorkflow],
            activities=[
                _mock_execute_scrape_map,
                _mock_parse_excel,
                _mock_validate,
                _mock_emit,
                _mock_spawn_sandbox,
                _mock_cleanup_sandbox,
                _mock_persist_result,
            ],
            activity_executor=ThreadPoolExecutor(max_workers=2),
        ):
            handle = await env.client.start_workflow(
                ScrapeJobWorkflow.run,
                input_,
                id=input_.job_id,
                task_queue="replay-test-queue",
            )
            await handle.result()

        history = await handle.fetch_history()

    replayer = Replayer(
        workflows=[ScrapeJobWorkflow],
        data_converter=pydantic_data_converter,
    )
    await replayer.replay_workflow(history)


@pytest.mark.asyncio
async def test_map_bank_workflow_importable() -> None:
    assert MapBankWorkflow is not None


@pytest.mark.asyncio
async def test_remap_bank_workflow_importable() -> None:
    assert RemapBankWorkflow is not None
