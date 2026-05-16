"""Activity definitions for open-banca Temporal workflows.

All 15 canonical activities from docs/02-components/orchestrator.md:

Activity inventory (orchestrator.md §Inventario):
  LoginActivity             — exp backoff, 2 attempts, 90s start-to-close, 10s heartbeat
  OTPSignalAwaitActivity    — no retry, 4min start-to-close, 15s heartbeat (ADR-0019)
  HumanInputAwaitActivity   — no retry, timeout_s+30s start-to-close, 15s heartbeat (ADR-0021)
  NavigateActivity          — exp backoff, 3 attempts, 30s start-to-close, 5s heartbeat
  DownloadExcelActivity     — exp backoff, 3 attempts, 2min start-to-close, 10s heartbeat
  ParseExcelActivity        — 1 attempt (deterministic), 60s start-to-close, threadpool
  ValidateActivity          — 2 attempts, 60s start-to-close
  JudgeActivity             — 1 attempt, 30s start-to-close
  MapperAgentActivity       — no retry, 20min start-to-close, 30s heartbeat
  RemapperAgentActivity     — no retry, 15min start-to-close, 30s heartbeat
  EmitWebhookActivity       — exp backoff, 5 attempts, 10s start-to-close, max 1h
  SpawnSandboxActivity      — 2 attempts, 60s start-to-close
  CleanupSandboxActivity    — 2 attempts, 30s start-to-close (idempotent)
  PersistResultActivity     — 3 attempts, 30s start-to-close (UPSERT)
  ListAccountsActivity      — 2 attempts, 30s start-to-close
"""

from open_banca_orchestrator.activities.cleanup_sandbox import (
    CleanupSandboxActivity,
    CleanupSandboxInput,
    CleanupSandboxResult,
    cleanup_sandbox,
)
from open_banca_orchestrator.activities.download_excel import (
    DownloadExcelActivity,
    DownloadExcelInput,
    DownloadExcelResult,
    download_excel,
)
from open_banca_orchestrator.activities.emit_webhook import (
    EmitWebhookActivity,
    EmitWebhookInput,
    EmitWebhookResult,
    emit_webhook,
)
from open_banca_orchestrator.activities.human_input_await import (
    HumanInputAwaitActivity,
    HumanInputAwaitInput,
    HumanInputAwaitResult,
    human_input_await,
)
from open_banca_orchestrator.activities.judge import (
    JudgeActivity,
    JudgeInput,
    JudgeResult,
    judge,
)
from open_banca_orchestrator.activities.list_accounts import (
    AccountInfo,
    ListAccountsActivity,
    ListAccountsInput,
    ListAccountsResult,
    list_accounts,
)
from open_banca_orchestrator.activities.login import (
    LoginActivity,
    LoginInput,
    LoginResult,
    login,
)
from open_banca_orchestrator.activities.mapper_agent import (
    MapperAgentActivity,
    MapperAgentInput,
    MapperAgentResult,
    mapper_agent,
)
from open_banca_orchestrator.activities.navigate import (
    NavigateActivity,
    NavigateInput,
    NavigateResult,
    navigate,
)
from open_banca_orchestrator.activities.otp_signal_await import (
    OTPSignalAwaitActivity,
    OTPSignalAwaitInput,
    OTPSignalAwaitResult,
    otp_signal_await,
)
from open_banca_orchestrator.activities.parse_excel import (
    ParseExcelActivity,
    ParseExcelInput,
    ParseExcelResult,
    parse_excel,
)
from open_banca_orchestrator.activities.persist_result import (
    PersistAccountInfo,
    PersistResultActivity,
    PersistResultInput,
    PersistResultResult,
    persist_result,
)
from open_banca_orchestrator.activities.remapper_agent import (
    RemapperAgentActivity,
    RemapperAgentInput,
    RemapperAgentResult,
    remapper_agent,
)
from open_banca_orchestrator.activities.spawn_sandbox import (
    SpawnSandboxActivity,
    SpawnSandboxInput,
    SpawnSandboxResult,
    spawn_sandbox,
)
from open_banca_orchestrator.activities.validate import (
    ValidateActivity,
    ValidateInput,
    ValidateResult,
    validate,
)

__all__ = [
    "AccountInfo",
    # Cleanup sandbox
    "CleanupSandboxActivity",
    "CleanupSandboxInput",
    "CleanupSandboxResult",
    # Download
    "DownloadExcelActivity",
    "DownloadExcelInput",
    "DownloadExcelResult",
    # Emit webhook
    "EmitWebhookActivity",
    "EmitWebhookInput",
    "EmitWebhookResult",
    # Human input await (ADR-0021)
    "HumanInputAwaitActivity",
    "HumanInputAwaitInput",
    "HumanInputAwaitResult",
    # Judge
    "JudgeActivity",
    "JudgeInput",
    "JudgeResult",
    # List accounts
    "ListAccountsActivity",
    "ListAccountsInput",
    "ListAccountsResult",
    # Login
    "LoginActivity",
    "LoginInput",
    "LoginResult",
    # Mapper agent
    "MapperAgentActivity",
    "MapperAgentInput",
    "MapperAgentResult",
    # Navigate
    "NavigateActivity",
    "NavigateInput",
    "NavigateResult",
    # OTP await
    "OTPSignalAwaitActivity",
    "OTPSignalAwaitInput",
    "OTPSignalAwaitResult",
    # Parse
    "ParseExcelActivity",
    "ParseExcelInput",
    "ParseExcelResult",
    # Persist result
    "PersistAccountInfo",
    "PersistResultActivity",
    "PersistResultInput",
    "PersistResultResult",
    # Remapper agent
    "RemapperAgentActivity",
    "RemapperAgentInput",
    "RemapperAgentResult",
    # Spawn sandbox
    "SpawnSandboxActivity",
    "SpawnSandboxInput",
    "SpawnSandboxResult",
    # Validate
    "ValidateActivity",
    "ValidateInput",
    "ValidateResult",
    "cleanup_sandbox",
    "download_excel",
    "emit_webhook",
    "human_input_await",
    "judge",
    "list_accounts",
    "login",
    "mapper_agent",
    "navigate",
    "otp_signal_await",
    "parse_excel",
    "persist_result",
    "remapper_agent",
    "spawn_sandbox",
    "validate",
]
