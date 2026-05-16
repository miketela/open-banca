"""Tests for ScrapeJobWorkflow human_input_provided signal (ADR-0021).

Uses Temporal time-skipping WorkflowEnvironment.

TDD coverage:
1. human_input_provided signal sets correct workflow state variables.
2. Signal carries payload: field_key, answer, persist.
3. Workflow get_status returns correct value after human_input signal.
4. Multiple signals: second human_input_provided overwrites first.
"""

from __future__ import annotations

import pytest

from open_banca_orchestrator.workflows.scrape_job import ScrapeJobWorkflow

# ── Unit tests on signal handler (no Temporal environment needed) ──────────────


@pytest.mark.asyncio
async def test_human_input_provided_signal_sets_state() -> None:
    """signal_human_input_provided sets _human_input_field_key and _human_input_answer."""
    wf = ScrapeJobWorkflow()
    await wf.signal_human_input_provided(
        field_key="security_q_pet",
        answer="Fluffy",
        persist=True,
    )
    assert wf._human_input_field_key == "security_q_pet"
    assert wf._human_input_answer == "Fluffy"
    assert wf._human_input_persist is True


@pytest.mark.asyncio
async def test_human_input_persist_default_true() -> None:
    """persist defaults to True when not provided."""
    wf = ScrapeJobWorkflow()
    await wf.signal_human_input_provided(
        field_key="security_q_pet",
        answer="Fluffy",
    )
    assert wf._human_input_persist is True


@pytest.mark.asyncio
async def test_human_input_persist_false() -> None:
    """persist=False is respected."""
    wf = ScrapeJobWorkflow()
    await wf.signal_human_input_provided(
        field_key="security_q_pet",
        answer="Fluffy",
        persist=False,
    )
    assert wf._human_input_persist is False


@pytest.mark.asyncio
async def test_get_status_after_human_input() -> None:
    """get_status returns 'resumed' after human_input_provided signal."""
    wf = ScrapeJobWorkflow()
    # Before signal
    assert wf.get_status() == "running"

    await wf.signal_human_input_provided(
        field_key="security_q_pet",
        answer="Fluffy",
    )
    # After signal
    assert wf.get_status() == "resumed"


@pytest.mark.asyncio
async def test_second_human_input_signal_overwrites_first() -> None:
    """Sending human_input_provided twice overwrites the first signal's data."""
    wf = ScrapeJobWorkflow()
    await wf.signal_human_input_provided(field_key="field_a", answer="first")
    await wf.signal_human_input_provided(field_key="field_b", answer="second")
    assert wf._human_input_field_key == "field_b"
    assert wf._human_input_answer == "second"


@pytest.mark.asyncio
async def test_cancel_overrides_human_input_in_status() -> None:
    """cancel_job status takes precedence over human_input_provided."""
    wf = ScrapeJobWorkflow()
    await wf.signal_human_input_provided(field_key="field_a", answer="answer")
    await wf.signal_cancel_job(reason="operator")
    assert wf.get_status() == "cancelled"


def test_workflow_initial_human_input_state() -> None:
    """Newly constructed workflow has no human_input state."""
    wf = ScrapeJobWorkflow()
    assert wf._human_input_field_key is None
    assert wf._human_input_answer is None
    assert wf._human_input_persist is True
