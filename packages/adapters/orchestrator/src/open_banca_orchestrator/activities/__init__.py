"""Activity definitions for open-banca Temporal workflows.

All 11 canonical activities from docs/02-components/orchestrator.md, plus
placeholder stubs for activities implemented in future tasks.

Activity inventory (orchestrator.md §Inventario):
  LoginActivity             — exp backoff, 2 attempts, 90s start-to-close, 10s heartbeat
  OTPSignalAwaitActivity    — no retry, 4min start-to-close, 15s heartbeat (ADR-0019)
  HumanInputAwaitActivity   — no retry, timeout_s+30s start-to-close, 15s heartbeat (ADR-0021)
  NavigateActivity          — exp backoff, 3 attempts, 30s start-to-close, 5s heartbeat
  DownloadExcelActivity     — exp backoff, 3 attempts, 2min start-to-close, 10s heartbeat
  ParseExcelActivity        — 1 attempt (deterministic), 60s start-to-close, threadpool
  ValidateActivity          — 2 attempts, 60s start-to-close    (PLACEHOLDER — task 19)
  JudgeActivity             — 1 attempt, 30s start-to-close      (PLACEHOLDER — task 19)
  MapperAgentActivity       — no retry, 20min start-to-close, 30s heartbeat  (task 14)
  RemapperAgentActivity     — no retry, 15min start-to-close, 30s heartbeat  (task 20)
  EmitWebhookActivity       — exp backoff, 5 attempts, 10s start-to-close, max 1h
"""

from open_banca_orchestrator.activities.human_input_await import (
    HumanInputAwaitActivity,
    HumanInputAwaitInput,
    HumanInputAwaitResult,
    human_input_await,
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
from open_banca_orchestrator.activities.judge import (
    JudgeActivity,
    JudgeInput,
    JudgeResult,
    judge,
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
from open_banca_orchestrator.activities.remapper_agent import (
    RemapperAgentActivity,
    RemapperAgentInput,
    RemapperAgentResult,
    remapper_agent,
)
from open_banca_orchestrator.activities.validate import (
    ValidateActivity,
    ValidateInput,
    ValidateResult,
    validate,
)

__all__ = [
    # Login
    "LoginActivity",
    "LoginInput",
    "LoginResult",
    "login",
    # Human input await (ADR-0021)
    "HumanInputAwaitActivity",
    "HumanInputAwaitInput",
    "HumanInputAwaitResult",
    "human_input_await",
    # OTP await
    "OTPSignalAwaitActivity",
    "OTPSignalAwaitInput",
    "OTPSignalAwaitResult",
    "otp_signal_await",
    # Navigate
    "NavigateActivity",
    "NavigateInput",
    "NavigateResult",
    "navigate",
    # Download
    "DownloadExcelActivity",
    "DownloadExcelInput",
    "DownloadExcelResult",
    "download_excel",
    # Parse
    "ParseExcelActivity",
    "ParseExcelInput",
    "ParseExcelResult",
    "parse_excel",
    # Validate
    "ValidateActivity",
    "ValidateInput",
    "ValidateResult",
    "validate",
    # Judge
    "JudgeActivity",
    "JudgeInput",
    "JudgeResult",
    "judge",
    # Mapper agent
    "MapperAgentActivity",
    "MapperAgentInput",
    "MapperAgentResult",
    "mapper_agent",
    # Remapper agent
    "RemapperAgentActivity",
    "RemapperAgentInput",
    "RemapperAgentResult",
    "remapper_agent",
    # Emit webhook
    "EmitWebhookActivity",
    "EmitWebhookInput",
    "EmitWebhookResult",
    "emit_webhook",
]
