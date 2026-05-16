"""Determinism tests — verify ScrapeJobWorkflow replay is non-deterministic-error-free.

Strategy:
  1. Run ScrapeJobWorkflow once under time-skipping WorkflowEnvironment.
  2. Fetch the workflow's event history.
  3. Replay through temporalio.worker.Replayer.
  4. Assert no NondeterminismError is raised.

This catches regressions where someone adds non-deterministic code to the
workflow (e.g. datetime.now(), random.random(), direct I/O).

Per ADR-0003 §Mitigaciones: "Tests obligatorios con WorkflowEnvironment para
todos los workflows críticos antes de merge."
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker
from workflow_activity_mocks import WORKFLOW_MOCK_ACTIVITIES_HAPPY

from open_banca_orchestrator.workflows.map_bank import MapBankWorkflow
from open_banca_orchestrator.workflows.remap_bank import RemapBankWorkflow
from open_banca_orchestrator.workflows.scrape_job import (
    ScrapeJobInput,
    ScrapeJobWorkflow,
    ScrapeMode,
)


@pytest.mark.asyncio
async def test_scrape_job_workflow_replay_is_deterministic() -> None:
    """ScrapeJobWorkflow history can be replayed without NondeterminismError.

    Runs the happy path, captures the event history, then replays it through
    the Replayer. Any non-deterministic code in the workflow will raise a
    NondeterminismError during replay, failing this test.
    """
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
            activities=WORKFLOW_MOCK_ACTIVITIES_HAPPY,
            activity_executor=ThreadPoolExecutor(max_workers=2),
        ):
            handle = await env.client.start_workflow(
                ScrapeJobWorkflow.run,
                input_,
                id=input_.job_id,
                task_queue="replay-test-queue",
            )
            await handle.result()

        # Fetch history after worker completes
        history = await handle.fetch_history()

    # Replay the history — raises NondeterminismError if code is non-deterministic
    # Use pydantic_data_converter to handle Pydantic v2 models (datetime.date, etc.)
    replayer = Replayer(
        workflows=[ScrapeJobWorkflow],
        data_converter=pydantic_data_converter,
    )
    await replayer.replay_workflow(history)
    # If we reach here, replay succeeded — workflow is deterministic


@pytest.mark.asyncio
async def test_map_bank_workflow_importable() -> None:
    """MapBankWorkflow is registered and importable (skeleton check)."""
    assert MapBankWorkflow is not None


@pytest.mark.asyncio
async def test_remap_bank_workflow_importable() -> None:
    """RemapBankWorkflow is registered and importable (skeleton check)."""
    assert RemapBankWorkflow is not None
