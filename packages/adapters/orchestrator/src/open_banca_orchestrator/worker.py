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
import os
import signal
import sys
from concurrent.futures import ThreadPoolExecutor

from temporalio.worker import Worker

from open_banca_orchestrator.activities import (
    cleanup_sandbox,
    download_excel,
    emit_webhook,
    human_input_await,
    judge,
    list_accounts,
    login,
    mapper_agent,
    navigate,
    otp_signal_await,
    parse_excel,
    persist_result,
    remapper_agent,
    spawn_sandbox,
    validate,
)
from open_banca_orchestrator.client import get_client
from open_banca_orchestrator.config import OrchestratorSettings, get_settings
from open_banca_orchestrator.workflows import (
    MapBankWorkflow,
    RemapBankWorkflow,
    ScrapeJobWorkflow,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Activity registry — 14 async + 1 sync (orchestrator.md §Inventario)
# ---------------------------------------------------------------------------
# ParseExcelActivity is synchronous (openpyxl blocking I/O) and must run in a
# ThreadPoolExecutor.  All other activities are async.
_ASYNC_ACTIVITIES = [
    login,  # LoginActivity
    otp_signal_await,  # OTPSignalAwaitActivity (long-running, 4 min, heartbeat 15s)
    human_input_await,  # HumanInputAwaitActivity (long-running, ADR-0021)
    navigate,  # NavigateActivity
    download_excel,  # DownloadExcelActivity
    validate,  # ValidateActivity
    judge,  # JudgeActivity
    mapper_agent,  # MapperAgentActivity
    remapper_agent,  # RemapperAgentActivity
    emit_webhook,  # EmitWebhookActivity
    spawn_sandbox,  # SpawnSandboxActivity
    cleanup_sandbox,  # CleanupSandboxActivity
    persist_result,  # PersistResultActivity
    list_accounts,  # ListAccountsActivity
]

_SYNC_ACTIVITIES = [
    parse_excel,  # ParseExcelActivity — sync, threadpool (openpyxl)
]


def _build_interceptors() -> list[object]:
    """Return OTel interceptors when OPEN_BANCA_OTEL_ENABLED is set.

    Uses ``temporalio.contrib.opentelemetry.TracingInterceptor`` to propagate
    trace context through workflow→activity spans automatically.  Falls back to
    an empty list when OTel is disabled or the contrib package is absent.
    """
    if os.environ.get("OPEN_BANCA_OTEL_ENABLED", "").lower() not in {"1", "true", "yes"}:
        return []

    try:
        from temporalio.contrib.opentelemetry import (
            TracingInterceptor,  # type: ignore[import-untyped]
        )

        from open_banca_observability.tracing import setup_tracer  # type: ignore[import-untyped]

        setup_tracer("orchestrator")
        return [TracingInterceptor()]
    except ImportError:
        logger.debug("temporalio.contrib.opentelemetry not available — OTel skipped")
        return []


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

    interceptors = _build_interceptors()
    client = await get_client(cfg)

    # ThreadPoolExecutor for synchronous activities (ParseExcelActivity).
    # Bounded to avoid resource exhaustion; sync activities are CPU+IO bounded.
    thread_pool = ThreadPoolExecutor(
        max_workers=min(4, cfg.worker_max_concurrent_activities),
        thread_name_prefix="open-banca-sync-activity",
    )

    worker = Worker(
        client,
        task_queue=cfg.temporal_task_queue,
        workflows=[
            ScrapeJobWorkflow,
            MapBankWorkflow,
            RemapBankWorkflow,
        ],
        activities=[*_ASYNC_ACTIVITIES, *_SYNC_ACTIVITIES],
        activity_executor=thread_pool,
        max_concurrent_activities=cfg.worker_max_concurrent_activities,
        max_concurrent_workflow_tasks=cfg.worker_max_concurrent_workflows,
        interceptors=interceptors,  # type: ignore[arg-type]
    )

    logger.info(
        "worker started",
        extra={
            "task_queue": cfg.temporal_task_queue,
            "workflows": ["ScrapeJobWorkflow", "MapBankWorkflow", "RemapBankWorkflow"],
            "activities": len(_ASYNC_ACTIVITIES) + len(_SYNC_ACTIVITIES),
        },
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
