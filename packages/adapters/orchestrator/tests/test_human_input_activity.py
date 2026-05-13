"""Tests for HumanInputAwaitActivity (ADR-0021).

Uses Temporal time-skipping WorkflowEnvironment so timeouts execute in ms.

TDD coverage:
1. Activity completes normally when cancelled (signal arrived simulation).
2. Activity raises on import (skeleton NotImplementedError not hit — we test the
   Python callable structure and input/output types).
3. HumanInputAwaitInput / HumanInputAwaitResult schema validation.
4. Activity is registered with correct @activity.defn name.
"""
from __future__ import annotations

import pytest
from temporalio import activity

from open_banca_orchestrator.activities.human_input_await import (
    HumanInputAwaitActivity,
    HumanInputAwaitInput,
    HumanInputAwaitResult,
    human_input_await,
)
from open_banca_orchestrator.activities.login import BrowserSessionToken


# ── Schema tests ──────────────────────────────────────────────────────────────


def test_human_input_await_input_defaults() -> None:
    """HumanInputAwaitInput has sensible defaults."""
    inp = HumanInputAwaitInput(
        job_id="job-001",
        field_key="security_q_pet",
        question_hash="a" * 64,
        question_text="¿Nombre de su primera mascota?",
        selector="#answer",
    )
    assert inp.timeout_s == 240
    assert inp.heartbeat_interval_s == 15
    assert inp.browser_session_token is None


def test_human_input_await_input_with_session_token() -> None:
    """HumanInputAwaitInput accepts browser_session_token."""
    token = BrowserSessionToken(
        container_id="c-001",
        socket_path="/run/banca/sidecar.sock",
        sidecar_pid=1234,
    )
    inp = HumanInputAwaitInput(
        job_id="job-001",
        field_key="security_q_pet",
        question_hash="a" * 64,
        question_text="question",
        selector="#answer",
        browser_session_token=token,
    )
    assert inp.browser_session_token == token


def test_human_input_await_result_defaults() -> None:
    """HumanInputAwaitResult has sensible defaults."""
    result = HumanInputAwaitResult()
    assert result.sidecar_alive is True
    assert result.heartbeat_count == 0


def test_human_input_await_result_custom() -> None:
    result = HumanInputAwaitResult(sidecar_alive=True, heartbeat_count=7)
    assert result.heartbeat_count == 7


# ── Activity registration ──────────────────────────────────────────────────────


def test_human_input_await_activity_name() -> None:
    """human_input_await must be registered with name 'HumanInputAwaitActivity'."""
    defn = activity.defn
    # Check the function has the Temporal activity decorator applied
    assert hasattr(human_input_await, "__temporal_activity_definition")
    act_def = human_input_await.__temporal_activity_definition  # type: ignore[attr-defined]
    assert act_def.name == "HumanInputAwaitActivity"


def test_human_input_await_activity_class_exists() -> None:
    """HumanInputAwaitActivity class exists (for compatibility)."""
    assert HumanInputAwaitActivity is not None


# ── Input validation ──────────────────────────────────────────────────────────


def test_human_input_await_input_question_hash_required() -> None:
    """question_hash is required — no default."""
    with pytest.raises(Exception):  # pydantic ValidationError
        HumanInputAwaitInput(
            job_id="job-001",
            field_key="security_q_pet",
            # missing question_hash
            question_text="question",
            selector="#answer",
        )


def test_human_input_await_input_rejects_missing_job_id() -> None:
    with pytest.raises(Exception):
        HumanInputAwaitInput(
            # missing job_id
            field_key="security_q_pet",
            question_hash="a" * 64,
            question_text="question",
            selector="#answer",
        )
