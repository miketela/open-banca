"""Tests for ScrapeJobWorkflow re-scrape after validation breakage + remap approval."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from open_banca_domain.entities.bank_map import BankMap, StepSpec
from open_banca_domain.entities.breakage_event import BreakageEvent
from open_banca_orchestrator.activities.emit_webhook import EmitWebhookResult
from open_banca_orchestrator.activities.execute_scrape_map import ExecuteScrapeMapResult
from open_banca_orchestrator.activities.judge import JudgeResult
from open_banca_orchestrator.activities.load_bank_map import LoadBankMapResult
from open_banca_orchestrator.activities.parse_excel import ParseExcelResult
from open_banca_orchestrator.activities.persist_remap_map import PersistRemapMapResult
from open_banca_orchestrator.activities.persist_result import PersistResultResult
from open_banca_orchestrator.activities.remapper_agent import RemapperAgentResult
from open_banca_orchestrator.activities.spawn_sandbox import SpawnSandboxResult
from open_banca_orchestrator.activities.cleanup_sandbox import CleanupSandboxResult
from open_banca_orchestrator.activities.validate import (
    ValidateResult,
    ValidationIssue,
    ValidationStatus,
)
from open_banca_orchestrator.workflows.map_bank import MapBankWorkflow
from open_banca_orchestrator.workflows.remap_bank import RemapBankWorkflow
from open_banca_orchestrator.workflows.scrape_job import (
    ScrapeJobInput,
    ScrapeJobResult,
    ScrapeJobWorkflow,
    ScrapeMode,
)

_SCRAPE_CALLS = 0
_VALIDATE_CALLS = 0
_LAST_PROPOSAL_ID: str | None = None
_TEST_ENV_CLIENT = None

_FAKE_EXECUTE = ExecuteScrapeMapResult(
    status="completed",
    excel_path="/tmp/test.xlsx",
    file_size_bytes=1024,
    content_hash="abc123",
    steps_completed=3,
)
_FAKE_PARSE = ParseExcelResult(transactions=[], row_count=0)
_FAKE_EMIT = EmitWebhookResult(enqueued=True, event_id="evt-fake")
_FAKE_SPAWN = SpawnSandboxResult(
    container_id="sandbox-001", sidecar_socket_path="/run/banca/sidecar.sock"
)
_FAKE_CLEANUP = CleanupSandboxResult(cleaned=True)
_FAKE_PERSIST = PersistResultResult(persisted_accounts=1, persisted_transactions=0)

_BANK_MAP = BankMap(
    bank_id="banco_general",
    version="1.0.0",
    schema_version="1",
    steps=[StepSpec(step_id="s1", action="click", target="#btn")],
)
_UPDATED_MAP = BankMap(
    bank_id="banco_general",
    version="1.0.1",
    schema_version="1",
    steps=[StepSpec(step_id="s1", action="click", target="#fixed")],
)


@activity.defn(name="ExecuteScrapeMapActivity")
async def _mock_execute_scrape_map(_input):  # type: ignore[no-untyped-def]
    global _SCRAPE_CALLS
    _SCRAPE_CALLS += 1
    return _FAKE_EXECUTE


@activity.defn(name="ParseExcelActivity")
def _mock_parse_excel(_input):  # type: ignore[no-untyped-def]
    return _FAKE_PARSE


@activity.defn(name="ValidateActivity")
async def _mock_validate(_input):  # type: ignore[no-untyped-def]
    global _VALIDATE_CALLS
    _VALIDATE_CALLS += 1
    if _VALIDATE_CALLS == 1:
        return ValidateResult(
            status=ValidationStatus.failed,
            issues=[
                ValidationIssue(code="balance_mismatch", message="totals do not match")
            ],
            validated_count=0,
            breakage_detected=True,
        )
    return ValidateResult(status=ValidationStatus.ok, validated_count=0)


@activity.defn(name="JudgeActivity")
async def _mock_judge(_input):  # type: ignore[no-untyped-def]
    return JudgeResult(
        route="human_required",
        confidence=0.5,
        risk="high",
        rationale="validation failure",
    )


@activity.defn(name="LoadBankMapActivity")
async def _mock_load_bank_map(_input):  # type: ignore[no-untyped-def]
    return LoadBankMapResult(bank_map=_BANK_MAP)


async def _auto_approve_child(proposal_id: str) -> None:
    assert _TEST_ENV_CLIENT is not None
    await asyncio.sleep(0)
    child = _TEST_ENV_CLIENT.get_workflow_handle(proposal_id)
    await child.signal(RemapBankWorkflow.signal_remap_approved, proposal_id)


@activity.defn(name="RemapperAgentActivity")
async def _mock_remapper(raw_input):  # type: ignore[no-untyped-def]
    global _LAST_PROPOSAL_ID
    from open_banca_orchestrator.activities.remapper_agent import RemapperAgentInput

    inp = (
        RemapperAgentInput.model_validate(raw_input)
        if isinstance(raw_input, dict)
        else raw_input
    )
    _LAST_PROPOSAL_ID = inp.proposal_id
    asyncio.create_task(_auto_approve_child(inp.proposal_id))
    return RemapperAgentResult(
        updated_map=_UPDATED_MAP,
        llm_cost_usd=0.05,
        diff_summary="patched",
        proposal_id=inp.proposal_id,
        applied=False,
    )


@activity.defn(name="PersistRemapMapActivity")
async def _mock_persist_remap(raw_input):  # type: ignore[no-untyped-def]
    from open_banca_orchestrator.activities.persist_remap_map import PersistRemapMapInput

    inp = (
        PersistRemapMapInput.model_validate(raw_input)
        if isinstance(raw_input, dict)
        else raw_input
    )
    return PersistRemapMapResult(
        map_path="/tmp/banco_general/map.json",
        version=inp.updated_map.version,
        persisted=True,
    )


@activity.defn(name="EmitWebhookActivity")
async def _mock_emit(_input):  # type: ignore[no-untyped-def]
    return _FAKE_EMIT


@activity.defn(name="SpawnSandboxActivity")
async def _mock_spawn(_input):  # type: ignore[no-untyped-def]
    return _FAKE_SPAWN


@activity.defn(name="CleanupSandboxActivity")
async def _mock_cleanup(_input):  # type: ignore[no-untyped-def]
    return _FAKE_CLEANUP


@activity.defn(name="PersistResultActivity")
async def _mock_persist_result(_input):  # type: ignore[no-untyped-def]
    return _FAKE_PERSIST


_ALL_ACTIVITIES = [
    _mock_execute_scrape_map,
    _mock_parse_excel,
    _mock_validate,
    _mock_judge,
    _mock_load_bank_map,
    _mock_remapper,
    _mock_persist_remap,
    _mock_emit,
    _mock_spawn,
    _mock_cleanup,
    _mock_persist_result,
]


async def _signal_remap_child(_env: WorkflowEnvironment) -> None:
    """Legacy helper — auto-approve now runs from remapper mock."""
    return None


@pytest.fixture(autouse=True)
def reset_counters() -> None:
    global _SCRAPE_CALLS, _VALIDATE_CALLS, _LAST_PROPOSAL_ID, _TEST_ENV_CLIENT
    _SCRAPE_CALLS = 0
    _VALIDATE_CALLS = 0
    _LAST_PROPOSAL_ID = None
    _TEST_ENV_CLIENT = None


@pytest.mark.asyncio
async def test_scrape_reruns_execute_map_after_remap_approval(
    thread_pool: ThreadPoolExecutor,
) -> None:
    job_input = ScrapeJobInput(
        job_id="job-rescrape-001",
        bank_id="banco_general",
        credential_ref="cred-ref-001",
        mode=ScrapeMode.full_historical,
        account_filter=["acc-001"],
    )

    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        global _TEST_ENV_CLIENT
        _TEST_ENV_CLIENT = env.client
        async with Worker(
            env.client,
            task_queue="rescrape-queue",
            workflows=[ScrapeJobWorkflow, MapBankWorkflow, RemapBankWorkflow],
            activities=_ALL_ACTIVITIES,
            activity_executor=thread_pool,
        ):
            handle = await env.client.start_workflow(
                ScrapeJobWorkflow.run,
                job_input,
                id=job_input.job_id,
                task_queue="rescrape-queue",
            )
            result: ScrapeJobResult = await handle.result()

    assert result.status == "completed"
    assert _SCRAPE_CALLS == 2, "execute_scrape_map must run again after remap approval"
    assert _VALIDATE_CALLS == 2


@pytest.fixture
def thread_pool() -> ThreadPoolExecutor:
    with ThreadPoolExecutor(max_workers=4) as pool:
        yield pool
