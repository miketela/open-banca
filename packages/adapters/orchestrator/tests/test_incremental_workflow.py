"""Tests for incremental scrape workflow — ExecuteScrapeMapActivity path."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

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
    ScrapeJobResult,
    ScrapeJobWorkflow,
    ScrapeMode,
)


@activity.defn(name="ExecuteScrapeMapActivity")
async def _mock_execute_scrape_map(_input):  # type: ignore[no-untyped-def]
    return ExecuteScrapeMapResult(
        status="completed",
        excel_path="/tmp/test_incremental.xlsx",
        file_size_bytes=512,
        content_hash="abc123",
        steps_completed=2,
    )


@activity.defn(name="ParseExcelActivity")
def _mock_parse_excel(_input):  # type: ignore[no-untyped-def]
    return ParseExcelResult(transactions=[], row_count=0)


@activity.defn(name="ValidateActivity")
async def _mock_validate(_input):  # type: ignore[no-untyped-def]
    return ValidateResult(status=ValidationStatus.ok, validated_count=0)


@activity.defn(name="EmitWebhookActivity")
async def _mock_emit(_input):  # type: ignore[no-untyped-def]
    return EmitWebhookResult(enqueued=True, event_id="evt-incr-001")


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


_ALL_MOCK_ACTIVITIES = [
    _mock_execute_scrape_map,
    _mock_parse_excel,
    _mock_validate,
    _mock_emit,
    _mock_spawn_sandbox,
    _mock_cleanup_sandbox,
    _mock_persist_result,
]


@pytest.fixture
def thread_pool() -> ThreadPoolExecutor:
    with ThreadPoolExecutor(max_workers=2) as pool:
        yield pool


@pytest.mark.asyncio
async def test_workflow_incremental_completes(thread_pool: ThreadPoolExecutor) -> None:
    incremental_input = ScrapeJobInput(
        job_id="test-incr-001",
        bank_id="banco_general",
        credential_ref="cred-ref-001",
        mode=ScrapeMode.incremental,
        since_cursor="2025-06-12",
        account_filter=["acc-001"],
    )

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue="test-queue",
            workflows=[ScrapeJobWorkflow, MapBankWorkflow, RemapBankWorkflow],
            activities=_ALL_MOCK_ACTIVITIES,
            activity_executor=thread_pool,
        ):
            result: ScrapeJobResult = await env.client.execute_workflow(
                ScrapeJobWorkflow.run,
                incremental_input,
                id=incremental_input.job_id,
                task_queue="test-queue",
                result_type=ScrapeJobResult,
            )

    assert result.status == "completed"
    assert len(result.errors) == 0


@pytest.mark.asyncio
async def test_workflow_full_historical_completes(thread_pool: ThreadPoolExecutor) -> None:
    full_input = ScrapeJobInput(
        job_id="test-full-001",
        bank_id="banco_general",
        credential_ref="cred-ref-001",
        mode=ScrapeMode.full_historical,
        account_filter=["acc-001"],
    )

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue="test-queue",
            workflows=[ScrapeJobWorkflow, MapBankWorkflow, RemapBankWorkflow],
            activities=_ALL_MOCK_ACTIVITIES,
            activity_executor=thread_pool,
        ):
            result: ScrapeJobResult = await env.client.execute_workflow(
                ScrapeJobWorkflow.run,
                full_input,
                id=full_input.job_id,
                task_queue="test-queue",
                result_type=ScrapeJobResult,
            )

    assert result.status == "completed"
    assert len(result.errors) == 0


@pytest.mark.asyncio
async def test_workflow_incremental_no_cursor_completes(
    thread_pool: ThreadPoolExecutor,
) -> None:
    incremental_no_cursor = ScrapeJobInput(
        job_id="test-incr-nocursor-001",
        bank_id="banco_general",
        credential_ref="cred-ref-001",
        mode=ScrapeMode.incremental,
        since_cursor=None,
        account_filter=["acc-001"],
    )

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue="test-queue",
            workflows=[ScrapeJobWorkflow, MapBankWorkflow, RemapBankWorkflow],
            activities=_ALL_MOCK_ACTIVITIES,
            activity_executor=thread_pool,
        ):
            result: ScrapeJobResult = await env.client.execute_workflow(
                ScrapeJobWorkflow.run,
                incremental_no_cursor,
                id=incremental_no_cursor.job_id,
                task_queue="test-queue",
                result_type=ScrapeJobResult,
            )

    assert result.status == "completed"
    assert len(result.errors) == 0
