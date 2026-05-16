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
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from open_banca_orchestrator.activities.download_excel import (
    DownloadExcelResult,
)
from open_banca_orchestrator.activities.emit_webhook import EmitWebhookResult
from open_banca_orchestrator.activities.login import (
    BrowserSessionToken,
    LoginResult,
    LoginStatus,
)
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
    ScrapeJobResult,
    ScrapeJobWorkflow,
    ScrapeMode,
)

# ---------------------------------------------------------------------------
# Mock activity implementations
# Each mock uses the exact @activity.defn(name=...) name as the real activity
# so the workflow's execute_activity calls route to these mocks.
# ---------------------------------------------------------------------------

_FAKE_SESSION_TOKEN = BrowserSessionToken(
    container_id="test-container",
    socket_path="/run/banca/sidecar.sock",
    sidecar_pid=1234,
)

_FAKE_NAVIGATE = NavigateResult(current_url="https://bank.test/txns", page_title="Transactions")
_FAKE_DOWNLOAD = DownloadExcelResult(
    excel_path="/tmp/test.xlsx",
    file_size_bytes=1024,
    content_hash="abc123",
)
_FAKE_PARSE = ParseExcelResult(transactions=[], row_count=0)
_FAKE_VALIDATE = ValidateResult(status=ValidationStatus.ok, validated_count=0)
_FAKE_EMIT = EmitWebhookResult(enqueued=True, event_id="evt-fake-001")
_FAKE_OTP_KEEPALIVE = OTPSignalAwaitResult(sidecar_alive=True, heartbeat_count=5)


@activity.defn(name="LoginActivity")
async def _mock_login_success(_input):  # type: ignore[no-untyped-def]
    return LoginResult(status=LoginStatus.success)


@activity.defn(name="LoginActivity")
async def _mock_login_needs_otp(_input):  # type: ignore[no-untyped-def]
    return LoginResult(
        status=LoginStatus.needs_otp,
        browser_session_token=_FAKE_SESSION_TOKEN,
    )


@activity.defn(name="LoginActivity")
async def _mock_login_failed(_input):  # type: ignore[no-untyped-def]
    return LoginResult(status=LoginStatus.failed, error_detail="bad creds")


@activity.defn(name="NavigateActivity")
async def _mock_navigate(_input):  # type: ignore[no-untyped-def]
    return _FAKE_NAVIGATE


@activity.defn(name="DownloadExcelActivity")
async def _mock_download(_input):  # type: ignore[no-untyped-def]
    return _FAKE_DOWNLOAD


@activity.defn(name="ParseExcelActivity")
def _mock_parse_excel(_input):  # type: ignore[no-untyped-def]  # sync
    return _FAKE_PARSE


@activity.defn(name="ValidateActivity")
async def _mock_validate(_input):  # type: ignore[no-untyped-def]
    return _FAKE_VALIDATE


@activity.defn(name="EmitWebhookActivity")
async def _mock_emit(_input):  # type: ignore[no-untyped-def]
    return _FAKE_EMIT


@activity.defn(name="OTPSignalAwaitActivity")
async def _mock_otp_keepalive(_input):  # type: ignore[no-untyped-def]
    return _FAKE_OTP_KEEPALIVE


_FAKE_SPAWN = SpawnSandboxResult(container_id="test-sandbox-001", sidecar_socket_path="/run/banca/sidecar.sock")
_FAKE_CLEANUP = CleanupSandboxResult(cleaned=True)
_FAKE_PERSIST = PersistResultResult(persisted_accounts=1, persisted_transactions=0)
_FAKE_LIST_ACCOUNTS = ListAccountsResult(accounts=[AccountInfo(account_id="acc-001")])


@activity.defn(name="SpawnSandboxActivity")
async def _mock_spawn_sandbox(_input):  # type: ignore[no-untyped-def]
    return _FAKE_SPAWN


@activity.defn(name="CleanupSandboxActivity")
async def _mock_cleanup_sandbox(_input):  # type: ignore[no-untyped-def]
    return _FAKE_CLEANUP


@activity.defn(name="PersistResultActivity")
async def _mock_persist_result(_input):  # type: ignore[no-untyped-def]
    return _FAKE_PERSIST


@activity.defn(name="ListAccountsActivity")
async def _mock_list_accounts(_input):  # type: ignore[no-untyped-def]
    return _FAKE_LIST_ACCOUNTS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_COMMON_MOCKS = [
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
]

_ALL_MOCK_ACTIVITIES_HAPPY = [_mock_login_success, *_COMMON_MOCKS]
_ALL_MOCK_ACTIVITIES_OTP = [_mock_login_needs_otp, *_COMMON_MOCKS]
_ALL_MOCK_ACTIVITIES_FAILED = [_mock_login_failed, *_COMMON_MOCKS]


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
            activities=_ALL_MOCK_ACTIVITIES_OTP,
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
            activities=_ALL_MOCK_ACTIVITIES_OTP,
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
            activities=_ALL_MOCK_ACTIVITIES_HAPPY,
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
