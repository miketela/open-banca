"""Tests for RemapperAgentActivity.

Covers:
  - test_hitl_only_v1: confidence 0.99 risk low → applied=False, RemapProposal created
  - test_activity_integration: workflow → activity → patch → proposal created
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from temporalio.testing import ActivityEnvironment

from open_banca_domain.entities.bank_map import BankMap, StepSpec
from open_banca_domain.entities.breakage_event import BreakageEvent
from open_banca_domain.entities.remap_proposal import RemapProposal, RemapStatus
from open_banca_orchestrator.activities.remapper_agent import (
    RemapperAgentInput,
    RemapperAgentResult,
    remapper_agent,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def current_map() -> BankMap:
    return BankMap(
        bank_id="banco-general",
        version="1.0.0",
        schema_version="1",
        steps=[
            StepSpec(step_id="s1", action="navigate", target="https://bancogeneral.com"),
            StepSpec(step_id="s2", action="fill", target="#username"),
            StepSpec(step_id="s3", action="click", target="#submit"),
        ],
    )


@pytest.fixture()
def breakage_event() -> BreakageEvent:
    return BreakageEvent(
        job_id="job-001",
        step_index=2,
        step_type="click",
        error_class="selector_not_found",
        screenshot_ref="sha256:abc123",
        dom_excerpt="<div><button id='btn-submit'>Login</button></div>",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture()
def remapper_input(current_map: BankMap, breakage_event: BreakageEvent) -> RemapperAgentInput:
    return RemapperAgentInput(
        job_id="job-001",
        bank_id="banco-general",
        breakage_hash="sha256:deadbeef",
        run_id="run-001",
        current_map=current_map,
        breakage_event=breakage_event,
        proposal_id="prop-test-001",
        judge_decision="partial_remap",
        sandbox_container_id="sandbox-001",
        llm_budget_usd=0.30,
        sensitive_data={"<USERNAME>": "user", "<PASSWORD>": "pass"},
    )


def _make_valid_patch_json() -> str:
    return json.dumps(
        {
            "target_step_index": 2,
            "new_steps": [
                {"step_id": "s3-fixed", "action": "click", "target": "#btn-submit"}
            ],
            "rationale": "Selector changed from #submit to #btn-submit",
            "confidence": 0.99,
            "risk": "low",
        }
    )


def _make_fake_remapper_agent(patch_json: str) -> Any:
    """Build a mock RemapperAgent that returns a fixed patch."""
    from open_banca_llm.remapper.agent import RemapPatch, RemapperResult  # noqa: PLC0415

    mock_agent = MagicMock()
    patch_obj = RemapPatch.model_validate(json.loads(patch_json))
    result_obj = RemapperResult(
        patch=patch_obj,
        cost_usd=0.05,
        bank_id="banco-general",
    )

    async def _fake_remap(*args: Any, **kwargs: Any) -> RemapperResult:
        return result_obj

    mock_agent.remap = _fake_remap
    return mock_agent


# ---------------------------------------------------------------------------
# test_hitl_only_v1
# ---------------------------------------------------------------------------


def _patch_activity_imports(
    fake_patch_json: str,
    dry_run_ok: bool = True,
    dry_run_issues: list[str] | None = None,
) -> Any:
    """Build a context manager that patches all lazy-imported symbols in the activity."""
    import contextlib  # noqa: PLC0415
    import sys  # noqa: PLC0415

    from open_banca_llm.remapper.dry_run import DryRunResult  # noqa: PLC0415

    fake_agent = _make_fake_remapper_agent(fake_patch_json)
    dry_result = DryRunResult(ok=dry_run_ok, issues=dry_run_issues or [])

    @contextlib.contextmanager
    def _ctx(
        proposals_sink: list[RemapProposal] | None = None,
        webhooks_sink: list[dict[str, Any]] | None = None,
    ) -> Any:
        import open_banca_llm.remapper.agent  # noqa: PLC0415, F401
        import open_banca_llm.remapper.dry_run  # noqa: PLC0415, F401
        import open_banca_orchestrator.activities.remapper_agent  # noqa: PLC0415, F401

        # Use sys.modules to get the actual module objects (avoids name collision)
        agent_mod = sys.modules["open_banca_llm.remapper.agent"]
        dry_run_mod = sys.modules["open_banca_llm.remapper.dry_run"]
        act_mod = sys.modules["open_banca_orchestrator.activities.remapper_agent"]

        def _persist(proposal: RemapProposal) -> None:
            if proposals_sink is not None:
                proposals_sink.append(proposal)

        def _emit(job_id: str, proposal: RemapProposal, cost: float) -> None:
            if webhooks_sink is not None:
                webhooks_sink.append(
                    {"job_id": job_id, "proposal_id": proposal.id, "cost_usd": cost}
                )

        with (
            patch.object(agent_mod, "RemapperAgent", return_value=fake_agent),
            patch.object(dry_run_mod, "validate_patch", return_value=dry_result),
            patch.object(act_mod, "_persist_proposal_impl", new=_persist),
            patch.object(act_mod, "_emit_webhook_impl", new=_emit),
            patch.object(act_mod, "_check_budget"),
        ):
            yield

    return _ctx


@pytest.mark.asyncio()
async def test_hitl_only_v1(
    remapper_input: RemapperAgentInput,
) -> None:
    """High confidence (0.99) + low risk → applied=False (v1 always HITL).

    A RemapProposal must be created and the webhook emitted.
    """
    env = ActivityEnvironment()

    created_proposals: list[RemapProposal] = []
    emitted_webhooks: list[dict[str, Any]] = []

    ctx = _patch_activity_imports(_make_valid_patch_json(), dry_run_ok=True)
    with ctx(proposals_sink=created_proposals, webhooks_sink=emitted_webhooks):
        result: RemapperAgentResult = await env.run(remapper_agent, remapper_input)

    # v1: NEVER auto-apply
    assert result.applied is False, "v1 HITL: applied must always be False"

    # RemapProposal was created
    assert len(created_proposals) == 1, "RemapProposal must be created"
    proposal = created_proposals[0]
    assert proposal.id == "prop-test-001"
    assert proposal.bank == "banco-general"
    assert proposal.status == RemapStatus.PENDING
    assert proposal.confidence == 0.99
    assert proposal.risk == "low"

    # Webhook was emitted
    assert len(emitted_webhooks) == 1, "job.remap_proposed webhook must be emitted"
    assert emitted_webhooks[0]["proposal_id"] == "prop-test-001"
    assert emitted_webhooks[0]["job_id"] == "job-001"

    # proposal_id returned in result
    assert result.proposal_id == "prop-test-001"


# ---------------------------------------------------------------------------
# test_activity_integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_activity_integration(
    remapper_input: RemapperAgentInput,
) -> None:
    """End-to-end: activity receives input → patch produced → proposal created.

    Uses ActivityEnvironment (Temporal) to run the activity function directly.
    """
    env = ActivityEnvironment()

    proposals: list[RemapProposal] = []

    ctx = _patch_activity_imports(_make_valid_patch_json(), dry_run_ok=True)
    with ctx(proposals_sink=proposals):
        result: RemapperAgentResult = await env.run(remapper_agent, remapper_input)

    # Activity returned a valid result
    assert isinstance(result, RemapperAgentResult)
    assert result.applied is False
    assert isinstance(result.updated_map, BankMap)
    assert result.llm_cost_usd >= 0.0
    assert result.diff_summary

    # Proposal was created
    assert len(proposals) == 1
    assert proposals[0].status == RemapStatus.PENDING


@pytest.mark.asyncio()
async def test_dry_run_failure_aborts_activity(
    remapper_input: RemapperAgentInput,
) -> None:
    """If dry-run fails, activity raises ApplicationError(non_retryable=True)."""
    from temporalio.exceptions import ApplicationError  # noqa: PLC0415

    env = ActivityEnvironment()

    ctx = _patch_activity_imports(
        _make_valid_patch_json(),
        dry_run_ok=False,
        dry_run_issues=["L04: embedded cred"],
    )
    with ctx():
        with pytest.raises(ApplicationError) as exc_info:
            await env.run(remapper_agent, remapper_input)

    assert exc_info.value.non_retryable is True
    assert "dry-run failed" in str(exc_info.value).lower()
