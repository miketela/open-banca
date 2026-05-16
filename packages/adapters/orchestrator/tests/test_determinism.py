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
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from open_banca_orchestrator.activities.download_excel import DownloadExcelResult
from open_banca_orchestrator.activities.emit_webhook import EmitWebhookResult
from open_banca_orchestrator.activities.login import LoginResult, LoginStatus
from open_banca_orchestrator.activities.navigate import NavigateResult
from open_banca_orchestrator.activities.otp_signal_await import OTPSignalAwaitResult
from open_banca_orchestrator.activities.parse_excel import ParseExcelResult
from open_banca_orchestrator.activities.validate import ValidateResult, ValidationStatus
from open_banca_orchestrator.activities.spawn_sandbox import SpawnSandboxResult
from open_banca_orchestrator.activities.cleanup_sandbox import CleanupSandboxResult
from open_banca_orchestrator.activities.persist_result import PersistResultResult
from open_banca_orchestrator.activities.list_accounts import ListAccountsResult, AccountInfo
from open_banca_orchestrator.workflows.map_bank import MapBankWorkflow
from open_banca_orchestrator.workflows.remap_bank import RemapBankWorkflow
from open_banca_orchestrator.workflows.scrape_job import (
    ScrapeJobInput,
    ScrapeJobWorkflow,
    ScrapeMode,
)

# ---------------------------------------------------------------------------
# Mock activities (same as test_scrape_workflow happy path)
# ---------------------------------------------------------------------------


@activity.defn(name="LoginActivity")
async def _mock_login_success(_input):  # type: ignore[no-untyped-def]
    return LoginResult(status=LoginStatus.success)


@activity.defn(name="NavigateActivity")
async def _mock_navigate(_input):  # type: ignore[no-untyped-def]
    return NavigateResult(current_url="https://bank.test/txns", page_title="Txns")


@activity.defn(name="DownloadExcelActivity")
async def _mock_download(_input):  # type: ignore[no-untyped-def]
    return DownloadExcelResult(
        excel_path="/tmp/test.xlsx", file_size_bytes=512, content_hash="abc"
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


@activity.defn(name="OTPSignalAwaitActivity")
async def _mock_otp_keepalive(_input):  # type: ignore[no-untyped-def]
    return OTPSignalAwaitResult(sidecar_alive=True, heartbeat_count=0)


@activity.defn(name="SpawnSandboxActivity")
async def _mock_spawn_sandbox(_input):  # type: ignore[no-untyped-def]
    return SpawnSandboxResult(container_id="test-sandbox", sidecar_socket_path="/run/banca/sidecar.sock")


@activity.defn(name="CleanupSandboxActivity")
async def _mock_cleanup_sandbox(_input):  # type: ignore[no-untyped-def]
    return CleanupSandboxResult(cleaned=True)


@activity.defn(name="PersistResultActivity")
async def _mock_persist_result(_input):  # type: ignore[no-untyped-def]
    return PersistResultResult(persisted_accounts=1, persisted_transactions=0)


@activity.defn(name="ListAccountsActivity")
async def _mock_list_accounts(_input):  # type: ignore[no-untyped-def]
    return ListAccountsResult(accounts=[AccountInfo(account_id="acc-001")])


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


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
            activities=[
                _mock_login_success,
                _mock_navigate,
                _mock_download,
                _mock_parse_excel,
                _mock_validate,
                _mock_emit,
                _mock_otp_keepalive,
                _mock_spawn_sandbox,
                _mock_cleanup_sandbox,
                _mock_persist_result,
                _mock_list_accounts,
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
