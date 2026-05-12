"""Tests for activity skeletons — each activity raises NotImplementedError.

Verifies:
  1. All 10 activities are importable and callable.
  2. Async activities raise NotImplementedError when invoked via ActivityEnvironment.
  3. ParseExcelActivity (sync) raises NotImplementedError when called directly.
  4. Pydantic input/output schemas validate correctly.
"""

from __future__ import annotations

import datetime

import pytest
from temporalio.testing import ActivityEnvironment

from open_banca_domain.entities.breakage_event import BreakageEvent
from open_banca_orchestrator.activities.download_excel import (
    DownloadExcelInput,
    DownloadPeriod,
    download_excel,
)
from open_banca_orchestrator.activities.emit_webhook import (
    EmitWebhookInput,
    WebhookEvent,
    WebhookEventType,
    emit_webhook,
)
from open_banca_orchestrator.activities.judge import JudgeInput
from open_banca_orchestrator.activities.login import (
    BrowserSessionToken,
    LoginInput,
    login,
)
from open_banca_orchestrator.activities.mapper_agent import (
    MapperAgentInput,
    mapper_agent,
)
from open_banca_orchestrator.activities.navigate import NavigateInput, navigate
from open_banca_orchestrator.activities.otp_signal_await import (
    OTPSignalAwaitInput,
    otp_signal_await,
)
from open_banca_orchestrator.activities.parse_excel import (
    ParseExcelInput,
    ParserConfig,
    parse_excel,
)
from open_banca_orchestrator.activities.remapper_agent import (
    RemapperAgentInput,
)
from open_banca_orchestrator.activities.validate import ValidateInput, validate

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SESSION_TOKEN = BrowserSessionToken(
    container_id="test-container",
    socket_path="/run/banca/sidecar.sock",
    sidecar_pid=9999,
)


@pytest.fixture
def env() -> ActivityEnvironment:
    return ActivityEnvironment()


# ---------------------------------------------------------------------------
# LoginActivity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_raises_not_implemented(env: ActivityEnvironment) -> None:
    """LoginActivity skeleton raises NotImplementedError."""
    input_ = LoginInput(
        job_id="job-001",
        bank_id="banco_general",
        credential_ref="cred-ref",
        nonce="abc123",
        sandbox_container_id="sandbox-001",
    )
    with pytest.raises(NotImplementedError):
        await env.run(login, input_)


def test_login_input_validates() -> None:
    """LoginInput Pydantic schema validates correctly."""
    i = LoginInput(
        job_id="j",
        bank_id="bg",
        credential_ref="cr",
        nonce="n",
        sandbox_container_id="s",
    )
    assert i.job_id == "j"


# ---------------------------------------------------------------------------
# OTPSignalAwaitActivity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_otp_signal_await_raises_not_implemented(env: ActivityEnvironment) -> None:
    """OTPSignalAwaitActivity skeleton raises NotImplementedError."""
    input_ = OTPSignalAwaitInput(
        job_id="job-001",
        browser_session_token=_SESSION_TOKEN,
    )
    with pytest.raises(NotImplementedError):
        await env.run(otp_signal_await, input_)


# ---------------------------------------------------------------------------
# NavigateActivity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_navigate_raises_not_implemented(env: ActivityEnvironment) -> None:
    """NavigateActivity skeleton raises NotImplementedError."""
    input_ = NavigateInput(
        job_id="job-001",
        step_id="step-001",
        bank_id="banco_general",
        account_id="acc-001",
        browser_session_token=_SESSION_TOKEN,
    )
    with pytest.raises(NotImplementedError):
        await env.run(navigate, input_)


# ---------------------------------------------------------------------------
# DownloadExcelActivity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_excel_raises_not_implemented(env: ActivityEnvironment) -> None:
    """DownloadExcelActivity skeleton raises NotImplementedError."""
    input_ = DownloadExcelInput(
        job_id="job-001",
        account_id="acc-001",
        period=DownloadPeriod(
            since=datetime.date(2024, 1, 1),
            until=datetime.date(2024, 12, 31),
        ),
        bank_id="banco_general",
        browser_session_token=_SESSION_TOKEN,
    )
    with pytest.raises(NotImplementedError):
        await env.run(download_excel, input_)


# ---------------------------------------------------------------------------
# ParseExcelActivity (sync)
# ---------------------------------------------------------------------------


def test_parse_excel_raises_not_implemented() -> None:
    """ParseExcelActivity is synchronous and raises NotImplementedError."""
    input_ = ParseExcelInput(
        excel_path="/tmp/test.xlsx",
        content_hash="deadbeef",
        account_id="acc-001",
        parser_config=ParserConfig(bank_id="banco_general"),
    )
    with pytest.raises(NotImplementedError):
        parse_excel(input_)


# ---------------------------------------------------------------------------
# ValidateActivity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_activity_empty_transactions(env: ActivityEnvironment) -> None:
    """ValidateActivity with empty transactions: heuristic path returns fail.

    Empty transaction set is a hard heuristic failure — no LLM required.
    This test does NOT import pydantic_ai to preserve Temporal sandbox isolation.
    """
    from open_banca_orchestrator.activities.validate import ValidationStatus  # noqa: PLC0415

    input_ = ValidateInput(
        job_id="job-001",
        account_id="acc-001",
        transactions=[],
        payload_hash="deadbeef",
    )
    result = await env.run(validate, input_)
    assert result.status == ValidationStatus.failed
    assert result.breakage_detected is True


# ---------------------------------------------------------------------------
# JudgeActivity
# NOTE: JudgeActivity always invokes LLM (no heuristic path).
#       Full tests with LLM mocking are in test_validate_judge_activities.py
#       which runs alphabetically after the Temporal sandbox tests to avoid
#       beartype import hook interference.
# ---------------------------------------------------------------------------


def test_judge_input_validates() -> None:
    """JudgeInput schema validates correctly with domain BreakageEvent."""
    import datetime  # noqa: PLC0415

    input_ = JudgeInput(
        job_id="job-001",
        breakage_event=BreakageEvent(
            job_id="job-001",
            step_index=0,
            step_type="navigate",
            error_class="layout_changed",
            screenshot_ref="sha256:abc",
            dom_excerpt="<div>broken layout</div>",
            occurred_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC),
        ),
        breakage_hash="deadbeef",
    )
    assert input_.job_id == "job-001"
    assert input_.breakage_event.error_class == "layout_changed"


# ---------------------------------------------------------------------------
# MapperAgentActivity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mapper_agent_raises_not_implemented(env: ActivityEnvironment) -> None:
    """MapperAgentActivity PLACEHOLDER raises NotImplementedError."""
    input_ = MapperAgentInput(
        job_id="job-001",
        bank_id="banco_general",
        run_id="run-001",
        sandbox_container_id="sandbox-001",
    )
    with pytest.raises(NotImplementedError):
        await env.run(mapper_agent, input_)


# ---------------------------------------------------------------------------
# RemapperAgentActivity
# ---------------------------------------------------------------------------


def test_remapper_agent_input_validates() -> None:
    """RemapperAgentInput schema validates correctly (task-20 implementation).

    The placeholder is replaced — activity now executes the full HITL flow.
    This test validates the input schema only (no activity execution).
    """
    import datetime  # noqa: PLC0415

    from open_banca_domain.entities.bank_map import BankMap as DomainBankMap  # noqa: PLC0415
    from open_banca_domain.entities.bank_map import StepSpec  # noqa: PLC0415
    from open_banca_domain.entities.breakage_event import BreakageEvent  # noqa: PLC0415

    input_ = RemapperAgentInput(
        job_id="job-001",
        bank_id="banco_general",
        breakage_hash="sha256:deadbeef",
        run_id="run-001",
        current_map=DomainBankMap(
            bank_id="banco_general",
            version="1.0.0",
            schema_version="1",
            steps=[StepSpec(step_id="s1", action="navigate")],
        ),
        breakage_event=BreakageEvent(
            job_id="job-001",
            step_index=0,
            step_type="navigate",
            error_class="layout_changed",
            screenshot_ref="sha256:abc",
            dom_excerpt="<div>broken</div>",
            occurred_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        ),
        proposal_id="prop-001",
        sandbox_container_id="sandbox-001",
    )
    assert input_.job_id == "job-001"
    assert input_.bank_id == "banco_general"
    assert input_.breakage_event.step_index == 0


# ---------------------------------------------------------------------------
# EmitWebhookActivity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_webhook_raises_not_implemented(env: ActivityEnvironment) -> None:
    """EmitWebhookActivity skeleton raises NotImplementedError."""
    input_ = EmitWebhookInput(
        event=WebhookEvent(
            event_id="evt-001",
            event_type=WebhookEventType.job_completed,
            job_id="job-001",
            timestamp="2026-01-01T00:00:00Z",
        )
    )
    with pytest.raises(NotImplementedError):
        await env.run(emit_webhook, input_)


# ---------------------------------------------------------------------------
# Schema validation smoke tests
# ---------------------------------------------------------------------------


def test_browser_session_token_schema() -> None:
    """BrowserSessionToken Pydantic model round-trips correctly."""
    token = BrowserSessionToken(
        container_id="c1",
        socket_path="/run/banca/sidecar.sock",
        sidecar_pid=42,
    )
    assert token.model_dump()["container_id"] == "c1"
    assert token.model_dump()["sidecar_pid"] == 42


def test_download_period_schema() -> None:
    """DownloadPeriod validates date ordering sanity."""
    period = DownloadPeriod(
        since=datetime.date(2024, 1, 1),
        until=datetime.date(2024, 12, 31),
    )
    assert period.since < period.until
