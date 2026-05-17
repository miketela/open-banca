"""Tests for ScrapeJobWorkflow — TDD-first per project conventions.

Uses Temporal's time-skipping WorkflowEnvironment. Activities are mocked via
Worker activity list (ExecuteScrapeMapActivity replaces login/navigate/download chain).

Test matrix:
  - test_happy_path:       execute_scrape_map succeeds → status=completed
  - test_human_input_timeout: execute returns human_input_timeout
  - test_cancel_signal:    cancel_job fires mid-flight → status=cancelled
  - test_remap_approved:   remap_approved signal processes (smoke)
  - test_execute_scrape_map_failed: activity returns failed → status=failed
"""

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

_FAKE_EXECUTE_OK = ExecuteScrapeMapResult(
    status="completed",
    excel_path="/tmp/test.xlsx",
    file_size_bytes=1024,
    content_hash="abc123",
    steps_completed=3,
)
_FAKE_PARSE = ParseExcelResult(transactions=[], row_count=0)
_FAKE_VALIDATE = ValidateResult(status=ValidationStatus.ok, validated_count=0)
_FAKE_EMIT = EmitWebhookResult(enqueued=True, event_id="evt-fake-001")
_FAKE_SPAWN = SpawnSandboxResult(
    container_id="test-sandbox-001", sidecar_socket_path="/run/banca/sidecar.sock"
)
_FAKE_CLEANUP = CleanupSandboxResult(cleaned=True)
_FAKE_PERSIST = PersistResultResult(persisted_accounts=1, persisted_transactions=0)


@activity.defn(name="ExecuteScrapeMapActivity")
async def _mock_execute_scrape_map_completed(_input):  # type: ignore[no-untyped-def]
    return _FAKE_EXECUTE_OK


@activity.defn(name="ExecuteScrapeMapActivity")
async def _mock_execute_scrape_map_failed(_input):  # type: ignore[no-untyped-def]
    return ExecuteScrapeMapResult(status="failed", errors=["scrape error"])


@activity.defn(name="ExecuteScrapeMapActivity")
async def _mock_execute_scrape_map_human_timeout(_input):  # type: ignore[no-untyped-def]
    return ExecuteScrapeMapResult(
        status="human_input_timeout",
        errors=["Human input not received within 240s"],
    )


@activity.defn(name="ParseExcelActivity")
def _mock_parse_excel(_input):  # type: ignore[no-untyped-def]
    return _FAKE_PARSE


@activity.defn(name="ValidateActivity")
async def _mock_validate(_input):  # type: ignore[no-untyped-def]
    return _FAKE_VALIDATE


@activity.defn(name="EmitWebhookActivity")
async def _mock_emit(_input):  # type: ignore[no-untyped-def]
    return _FAKE_EMIT


@activity.defn(name="SpawnSandboxActivity")
async def _mock_spawn_sandbox(_input):  # type: ignore[no-untyped-def]
    return _FAKE_SPAWN


@activity.defn(name="CleanupSandboxActivity")
async def _mock_cleanup_sandbox(_input):  # type: ignore[no-untyped-def]
    return _FAKE_CLEANUP


@activity.defn(name="PersistResultActivity")
async def _mock_persist_result(_input):  # type: ignore[no-untyped-def]
    return _FAKE_PERSIST


_COMMON_MOCKS = [
    _mock_parse_excel,
    _mock_validate,
    _mock_emit,
    _mock_spawn_sandbox,
    _mock_cleanup_sandbox,
    _mock_persist_result,
]

_ALL_MOCK_ACTIVITIES_HAPPY = [_mock_execute_scrape_map_completed, *_COMMON_MOCKS]
_ALL_MOCK_ACTIVITIES_FAILED = [_mock_execute_scrape_map_failed, *_COMMON_MOCKS]
_ALL_MOCK_ACTIVITIES_HUMAN_TIMEOUT = [_mock_execute_scrape_map_human_timeout, *_COMMON_MOCKS]


@pytest.fixture
def base_input() -> ScrapeJobInput:
    return ScrapeJobInput(
        job_id="test-job-001",
        bank_id="banco_general",
        credential_ref="cred-ref-001",
        mode=ScrapeMode.full_historical,
        account_filter=["acc-001"],
    )


@pytest.fixture
def thread_pool() -> ThreadPoolExecutor:
    with ThreadPoolExecutor(max_workers=2) as pool:
        yield pool


@pytest.mark.asyncio
async def test_happy_path(base_input: ScrapeJobInput, thread_pool: ThreadPoolExecutor) -> None:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue="test-queue",
            workflows=[ScrapeJobWorkflow, MapBankWorkflow, RemapBankWorkflow],
            activities=_ALL_MOCK_ACTIVITIES_HAPPY,
            activity_executor=thread_pool,
        ):
            result: ScrapeJobResult = await env.client.execute_workflow(
                ScrapeJobWorkflow.run,
                base_input,
                id=base_input.job_id,
                task_queue="test-queue",
                result_type=ScrapeJobResult,
            )

    assert result.status == "completed"
    assert result.job_id == base_input.job_id
    assert len(result.errors) == 0


@pytest.mark.asyncio
async def test_human_input_timeout(
    base_input: ScrapeJobInput, thread_pool: ThreadPoolExecutor
) -> None:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue="test-queue",
            workflows=[ScrapeJobWorkflow, MapBankWorkflow, RemapBankWorkflow],
            activities=_ALL_MOCK_ACTIVITIES_HUMAN_TIMEOUT,
            activity_executor=thread_pool,
        ):
            result: ScrapeJobResult = await env.client.execute_workflow(
                ScrapeJobWorkflow.run,
                base_input,
                id=base_input.job_id,
                task_queue="test-queue",
                result_type=ScrapeJobResult,
            )

    assert result.status == "human_input_timeout"
    assert result.errors


@pytest.mark.asyncio
async def test_cancel_signal(base_input: ScrapeJobInput, thread_pool: ThreadPoolExecutor) -> None:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue="test-queue",
            workflows=[ScrapeJobWorkflow, MapBankWorkflow, RemapBankWorkflow],
            activities=_ALL_MOCK_ACTIVITIES_HAPPY,
            activity_executor=thread_pool,
        ):
            handle = await env.client.start_workflow(
                ScrapeJobWorkflow.run,
                base_input,
                id=base_input.job_id,
                task_queue="test-queue",
            )
            await handle.signal(ScrapeJobWorkflow.signal_cancel_job, "test cancellation")
            result: ScrapeJobResult = await handle.result()

    assert result.status == "cancelled"
    assert any("Cancelled" in e for e in result.errors)


@pytest.mark.asyncio
async def test_execute_scrape_map_failed(
    base_input: ScrapeJobInput, thread_pool: ThreadPoolExecutor
) -> None:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue="test-queue",
            workflows=[ScrapeJobWorkflow, MapBankWorkflow, RemapBankWorkflow],
            activities=_ALL_MOCK_ACTIVITIES_FAILED,
            activity_executor=thread_pool,
        ):
            result: ScrapeJobResult = await env.client.execute_workflow(
                ScrapeJobWorkflow.run,
                base_input,
                id=base_input.job_id,
                task_queue="test-queue",
                result_type=ScrapeJobResult,
            )

    assert result.status == "failed"
    assert any("scrape" in e.lower() for e in result.errors)


@pytest.mark.asyncio
async def test_remap_approved_signal(
    base_input: ScrapeJobInput, thread_pool: ThreadPoolExecutor
) -> None:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue="test-queue",
            workflows=[ScrapeJobWorkflow, MapBankWorkflow, RemapBankWorkflow],
            activities=_ALL_MOCK_ACTIVITIES_HAPPY,
            activity_executor=thread_pool,
        ):
            handle = await env.client.start_workflow(
                ScrapeJobWorkflow.run,
                base_input,
                id=base_input.job_id,
                task_queue="test-queue",
            )
            await handle.signal(ScrapeJobWorkflow.signal_remap_approved, "proposal-001")
            result: ScrapeJobResult = await handle.result()

    assert result.job_id == base_input.job_id
