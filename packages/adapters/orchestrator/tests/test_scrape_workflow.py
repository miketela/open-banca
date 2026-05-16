"""Tests for ScrapeJobWorkflow — TDD-first per project conventions.

Uses Temporal's time-skipping WorkflowEnvironment so 4-minute OTP timeouts
execute in milliseconds.  All activities are mocked via activity_mocks dict
passed to env.run_workflow().

Test matrix:
  - test_happy_path:       all activities succeed → status=completed
  - test_otp_timeout:      otp_confirmed never arrives → status=otp_timeout
  - test_cancel_signal:    cancel_job fires mid-flight → status=cancelled
  - test_remap_approved:   remap_approved signal processes (smoke — skeleton)
  - test_login_failed:     LoginActivity returns failed → status=failed
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from workflow_activity_mocks import (
    WORKFLOW_MOCK_ACTIVITIES_FAILED,
    WORKFLOW_MOCK_ACTIVITIES_HAPPY,
    WORKFLOW_MOCK_ACTIVITIES_OTP,
)

from open_banca_orchestrator.workflows.map_bank import MapBankWorkflow
from open_banca_orchestrator.workflows.remap_bank import RemapBankWorkflow
from open_banca_orchestrator.workflows.scrape_job import (
    ScrapeJobInput,
    ScrapeJobResult,
    ScrapeJobWorkflow,
    ScrapeMode,
)


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
    """Thread pool for synchronous activities (ParseExcelActivity)."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        yield pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path(base_input: ScrapeJobInput, thread_pool: ThreadPoolExecutor) -> None:
    """All activities succeed → workflow returns status=completed."""
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue="test-queue",
            workflows=[ScrapeJobWorkflow, MapBankWorkflow, RemapBankWorkflow],
            activities=WORKFLOW_MOCK_ACTIVITIES_HAPPY,
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
async def test_otp_timeout(base_input: ScrapeJobInput, thread_pool: ThreadPoolExecutor) -> None:
    """When otp_confirmed signal never arrives within 4 min, workflow returns otp_timeout.

    Time-skipping env makes the 4-minute wait instant.
    """
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue="test-queue",
            workflows=[ScrapeJobWorkflow, MapBankWorkflow, RemapBankWorkflow],
            activities=WORKFLOW_MOCK_ACTIVITIES_OTP,
            activity_executor=thread_pool,
        ):
            result: ScrapeJobResult = await env.client.execute_workflow(
                ScrapeJobWorkflow.run,
                base_input,
                id=base_input.job_id,
                task_queue="test-queue",
                result_type=ScrapeJobResult,
            )

    assert result.status == "otp_timeout"
    assert any("OTP" in e for e in result.errors)


@pytest.mark.asyncio
async def test_cancel_signal(base_input: ScrapeJobInput, thread_pool: ThreadPoolExecutor) -> None:
    """cancel_job signal during OTP wait → workflow returns status=cancelled."""
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue="test-queue",
            workflows=[ScrapeJobWorkflow, MapBankWorkflow, RemapBankWorkflow],
            activities=WORKFLOW_MOCK_ACTIVITIES_OTP,
            activity_executor=thread_pool,
        ):
            handle = await env.client.start_workflow(
                ScrapeJobWorkflow.run,
                base_input,
                id=base_input.job_id,
                task_queue="test-queue",
            )
            # Send cancel signal immediately after starting
            await handle.signal(ScrapeJobWorkflow.signal_cancel_job, "test cancellation")
            result: ScrapeJobResult = await handle.result()

    assert result.status == "cancelled"
    assert any("Cancelled" in e for e in result.errors)


@pytest.mark.asyncio
async def test_login_failed(base_input: ScrapeJobInput, thread_pool: ThreadPoolExecutor) -> None:
    """LoginActivity returns failed → workflow returns status=failed."""
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue="test-queue",
            workflows=[ScrapeJobWorkflow, MapBankWorkflow, RemapBankWorkflow],
            activities=WORKFLOW_MOCK_ACTIVITIES_FAILED,
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
    assert any("Login failed" in e for e in result.errors)


@pytest.mark.asyncio
async def test_remap_approved_signal(
    base_input: ScrapeJobInput, thread_pool: ThreadPoolExecutor
) -> None:
    """remap_approved signal sets internal state correctly (signal handler smoke test).

    This test verifies the signal handler wiring, not the full remap workflow
    (which is wired in task 20).
    """
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue="test-queue",
            workflows=[ScrapeJobWorkflow, MapBankWorkflow, RemapBankWorkflow],
            activities=WORKFLOW_MOCK_ACTIVITIES_HAPPY,
            activity_executor=thread_pool,
        ):
            handle = await env.client.start_workflow(
                ScrapeJobWorkflow.run,
                base_input,
                id=base_input.job_id,
                task_queue="test-queue",
            )
            # Signal remap_approved — workflow should not crash
            await handle.signal(ScrapeJobWorkflow.signal_remap_approved, "proposal-001")
            result: ScrapeJobResult = await handle.result()

    # Workflow should complete (login was success, remap signal is accepted but
    # remap workflow is a skeleton — full remap flow tested in task 20)
    assert result.job_id == base_input.job_id
