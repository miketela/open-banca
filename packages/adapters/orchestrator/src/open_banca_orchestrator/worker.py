"""Temporal worker entry-point for open-banca.

Run as::

    python -m open_banca_orchestrator.worker

Environment variables (all prefixed ``OPEN_BANCA_``):

    OPEN_BANCA_TEMPORAL_ADDRESS      gRPC host:port  (default: localhost:7233)
    OPEN_BANCA_TEMPORAL_NAMESPACE    Temporal namespace  (default: default)
    OPEN_BANCA_TEMPORAL_TASK_QUEUE   Task queue name  (default: open-banca-task-queue)
    OPEN_BANCA_WORKER_MAX_CONCURRENT_ACTIVITIES  (default: 10)
    OPEN_BANCA_WORKER_MAX_CONCURRENT_WORKFLOWS   (default: 100)

-------------------------------------------------------------------------------
DETERMINISM NOTICE FOR FUTURE WORKFLOW AUTHORS
-------------------------------------------------------------------------------
Temporal replays workflow history to reconstruct state after a crash.  Code
running *inside* a ``@workflow.run`` coroutine MUST be deterministic across
replays.  Violating this causes ``NonDeterminismError`` and stalls the workflow.

Rules that MUST be followed in all ``workflows/`` modules:

  1. Time   — use ``workflow.now()`` NOT ``datetime.datetime.now()`` / ``time.time()``
  2. Random — use ``workflow.random()`` NOT ``random.random()`` / ``secrets.token_*``
  3. I/O    — NEVER do file I/O, network calls, or DB queries directly in a workflow.
              All side-effects go through activities (``workflow.execute_activity``).
  4. Threads/globals — no threading, no locks, no mutable module-level state.
  5. Imports — do NOT import modules with side-effects (e.g., ``import time``) in
               workflow coroutine bodies; put them at module top-level at most.

These rules are enforced via a lint policy (see ADR-0003 mitigations).
-------------------------------------------------------------------------------
"""

import asyncio
import logging
import signal
import sys

from temporalio.activity import defn as activity_defn
from temporalio.worker import Worker

from open_banca_orchestrator.client import get_client
from open_banca_orchestrator.config import OrchestratorSettings, get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Placeholder activity — required because Worker rejects empty activity lists.
# Remove once Tasks 9, 12, 14, 19, 20 register real activities.
# ---------------------------------------------------------------------------
@activity_defn
async def _noop_placeholder() -> None:  # pragma: no cover
    """Placeholder activity: keeps worker construction valid.

    This activity is never scheduled by a real workflow.  It exists solely so
    that ``Worker(activities=[...])`` receives a non-empty list during the
    Task-6 skeleton phase.  Delete this when the first real activity lands.
    """


async def run_worker(settings: OrchestratorSettings | None = None) -> None:
    """Connect to Temporal and run the worker until cancelled.

    Args:
        settings: Optional settings override (useful in tests).
    """
    cfg = settings or get_settings()

    _configure_logging()

    logger.info(
        "connecting to Temporal",
        extra={
            "temporal_address": cfg.temporal_address,
            "namespace": cfg.temporal_namespace,
            "task_queue": cfg.temporal_task_queue,
        },
    )

    client = await get_client(cfg)

    worker = Worker(
        client,
        task_queue=cfg.temporal_task_queue,
        # Workflows registered in tasks 9, 12 (ScrapeJobWorkflow, MapBankWorkflow,
        # RemapBankWorkflow).  Add them here when those tasks land.
        workflows=[],
        # Activities registered in tasks 14, 19, 20 (LoginActivity, NavigateActivity,
        # DownloadExcelActivity, ParseExcelActivity, ValidateActivity, JudgeActivity,
        # MapperAgentActivity, RemapperAgentActivity, EmitWebhookActivity,
        # OTPSignalAwaitActivity, and one more per PRD REQ-002).
        # Placeholder keeps Worker construction valid until then.
        activities=[_noop_placeholder],
        max_concurrent_activities=cfg.worker_max_concurrent_activities,
        max_concurrent_workflow_tasks=cfg.worker_max_concurrent_workflows,
    )

    logger.info(
        "worker started",
        extra={"task_queue": cfg.temporal_task_queue},
    )

    await worker.run()


def _configure_logging() -> None:
    """Set up structured logging in logfmt-compatible format.

    Production operators can redirect stdout to a log aggregator (Loki, CloudWatch).

    trace_id propagation: when OpenTelemetry is enabled (OPEN_BANCA_OTEL_ENABLED),
    inject ``trace_id`` and ``span_id`` into each LogRecord via the OTel log handler
    (see packages/adapters/observability).  The ``extra`` dicts used in this module
    are the hook points for that propagation.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="time=%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s",
        stream=sys.stdout,
    )


async def _amain() -> None:
    """Async entry-point with graceful SIGTERM handling."""
    loop = asyncio.get_running_loop()

    stop_event = asyncio.Event()

    def _handle_sigterm() -> None:
        logger.info("SIGTERM received — initiating graceful shutdown")
        stop_event.set()

    loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)
    loop.add_signal_handler(signal.SIGINT, _handle_sigterm)

    worker_task = asyncio.create_task(run_worker())

    # Wait for stop signal or worker exit (whichever comes first)
    done, pending = await asyncio.wait(
        [worker_task, asyncio.create_task(stop_event.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    # Re-raise any worker exception
    for task in done:
        if task is worker_task and not task.cancelled():
            exc = task.exception()
            if exc is not None:
                raise exc

    logger.info("worker shut down cleanly")


def main() -> None:
    """Synchronous entry-point for ``python -m open_banca_orchestrator.worker``."""
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
