"""Shared Temporal activity mocks for ScrapeJobWorkflow integration tests."""

from __future__ import annotations

from temporalio import activity

from open_banca_orchestrator.activities.cleanup_sandbox import CleanupSandboxResult
from open_banca_orchestrator.activities.download_excel import DownloadExcelResult
from open_banca_orchestrator.activities.emit_webhook import EmitWebhookResult
from open_banca_orchestrator.activities.list_accounts import AccountInfo, ListAccountsResult
from open_banca_orchestrator.activities.login import (
    BrowserSessionToken,
    LoginResult,
    LoginStatus,
)
from open_banca_orchestrator.activities.navigate import NavigateResult
from open_banca_orchestrator.activities.otp_signal_await import OTPSignalAwaitResult
from open_banca_orchestrator.activities.parse_excel import ParseExcelResult
from open_banca_orchestrator.activities.persist_result import PersistResultResult
from open_banca_orchestrator.activities.spawn_sandbox import SpawnSandboxResult
from open_banca_orchestrator.activities.validate import ValidateResult, ValidationStatus

_FAKE_SESSION_TOKEN = BrowserSessionToken(
    container_id="test-container",
    socket_path="/run/banca/sidecar.sock",
    sidecar_pid=1234,
)

_FAKE_NAVIGATE = NavigateResult(
    current_url="https://bank.test/txns",
    page_title="Transactions",
)
_FAKE_DOWNLOAD = DownloadExcelResult(
    excel_path="/tmp/test.xlsx",
    file_size_bytes=1024,
    content_hash="abc123",
)
_FAKE_PARSE = ParseExcelResult(transactions=[], row_count=0)
_FAKE_VALIDATE = ValidateResult(status=ValidationStatus.ok, validated_count=0)
_FAKE_EMIT = EmitWebhookResult(enqueued=True, event_id="evt-fake-001")
_FAKE_OTP_KEEPALIVE = OTPSignalAwaitResult(sidecar_alive=True, heartbeat_count=5)
_FAKE_SPAWN = SpawnSandboxResult(container_id="sandbox-test-001")
_FAKE_CLEANUP = CleanupSandboxResult(cleaned=True)
_FAKE_PERSIST = PersistResultResult(persisted_accounts=1, persisted_transactions=0)
_FAKE_LIST_ACCOUNTS = ListAccountsResult(
    accounts=[AccountInfo(account_id="acc-001", account_type="savings", label="Test")]
)


@activity.defn(name="SpawnSandboxActivity")
async def mock_spawn_sandbox(_input):  # type: ignore[no-untyped-def]
    return _FAKE_SPAWN


@activity.defn(name="CleanupSandboxActivity")
async def mock_cleanup_sandbox(_input):  # type: ignore[no-untyped-def]
    return _FAKE_CLEANUP


@activity.defn(name="PersistResultActivity")
async def mock_persist_result(_input):  # type: ignore[no-untyped-def]
    return _FAKE_PERSIST


@activity.defn(name="ListAccountsActivity")
async def mock_list_accounts(_input):  # type: ignore[no-untyped-def]
    return _FAKE_LIST_ACCOUNTS


@activity.defn(name="LoginActivity")
async def mock_login_success(_input):  # type: ignore[no-untyped-def]
    return LoginResult(status=LoginStatus.success)


@activity.defn(name="LoginActivity")
async def mock_login_needs_otp(_input):  # type: ignore[no-untyped-def]
    return LoginResult(
        status=LoginStatus.needs_otp,
        browser_session_token=_FAKE_SESSION_TOKEN,
    )


@activity.defn(name="LoginActivity")
async def mock_login_failed(_input):  # type: ignore[no-untyped-def]
    return LoginResult(status=LoginStatus.failed, error_detail="bad creds")


@activity.defn(name="NavigateActivity")
async def mock_navigate(_input):  # type: ignore[no-untyped-def]
    return _FAKE_NAVIGATE


@activity.defn(name="DownloadExcelActivity")
async def mock_download(_input):  # type: ignore[no-untyped-def]
    return _FAKE_DOWNLOAD


@activity.defn(name="ParseExcelActivity")
def mock_parse_excel(_input):  # type: ignore[no-untyped-def]
    return _FAKE_PARSE


@activity.defn(name="ValidateActivity")
async def mock_validate(_input):  # type: ignore[no-untyped-def]
    return _FAKE_VALIDATE


@activity.defn(name="EmitWebhookActivity")
async def mock_emit(_input):  # type: ignore[no-untyped-def]
    return _FAKE_EMIT


@activity.defn(name="OTPSignalAwaitActivity")
async def mock_otp_keepalive(_input):  # type: ignore[no-untyped-def]
    return _FAKE_OTP_KEEPALIVE


WORKFLOW_MOCK_ACTIVITIES_HAPPY = [
    mock_spawn_sandbox,
    mock_cleanup_sandbox,
    mock_persist_result,
    mock_list_accounts,
    mock_login_success,
    mock_navigate,
    mock_download,
    mock_parse_excel,
    mock_validate,
    mock_emit,
    mock_otp_keepalive,
]

WORKFLOW_MOCK_ACTIVITIES_OTP = [
    mock_spawn_sandbox,
    mock_cleanup_sandbox,
    mock_persist_result,
    mock_list_accounts,
    mock_login_needs_otp,
    mock_navigate,
    mock_download,
    mock_parse_excel,
    mock_validate,
    mock_emit,
    mock_otp_keepalive,
]

WORKFLOW_MOCK_ACTIVITIES_FAILED = [
    mock_spawn_sandbox,
    mock_cleanup_sandbox,
    mock_persist_result,
    mock_list_accounts,
    mock_login_failed,
    mock_navigate,
    mock_download,
    mock_parse_excel,
    mock_validate,
    mock_emit,
    mock_otp_keepalive,
]
