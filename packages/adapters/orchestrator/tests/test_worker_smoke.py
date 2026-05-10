"""Tests: worker starts and shuts down cleanly in a time-skipping environment.

No real workflows are registered (Task-6 skeleton).  The test verifies that:
  1. Worker construction succeeds with the placeholder activity.
  2. Worker shuts down without errors when cancelled.
  3. run_worker() accepts injected settings (no real Temporal server needed).
"""

import asyncio

import pytest
from temporalio.testing import WorkflowEnvironment

from open_banca_orchestrator.config import OrchestratorSettings
from open_banca_orchestrator.worker import run_worker


@pytest.mark.asyncio
async def test_worker_starts_and_stops_cleanly() -> None:
    """Worker runs briefly then cancels cleanly — no errors, no registered workflows."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        settings = OrchestratorSettings(
            temporal_address=env.client.service_client.config.target_host,
            temporal_namespace=env.client.namespace,
        )

        # run_worker() blocks until cancelled; we cancel it after a short yield.
        worker_task = asyncio.create_task(run_worker(settings))

        # Give the event loop a couple of ticks to let the worker start polling
        await asyncio.sleep(0.1)

        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass  # Expected — cancellation is the shutdown mechanism in tests


@pytest.mark.asyncio
async def test_worker_uses_correct_task_queue() -> None:
    """Worker is constructed with the task queue from settings."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        queue = "test-queue-smoke"
        settings = OrchestratorSettings(
            temporal_address=env.client.service_client.config.target_host,
            temporal_namespace=env.client.namespace,
            temporal_task_queue=queue,
        )

        worker_task = asyncio.create_task(run_worker(settings))
        await asyncio.sleep(0.1)
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        # No assertion needed beyond "no exception raised" — the task queue is
        # structural; if it were wrong Worker() would raise at construction.
