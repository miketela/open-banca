"""ScrapeJobWorkflow — root workflow for each POST /scrape request.

Orchestrates the full scraping pipeline per docs/02-components/orchestrator.md.

Signals:
  otp_confirmed         — legacy; retained for API compatibility.
  remap_approved        — POST /maps/{bank}/proposals/{id}/approve — resumes after remap.
  cancel_job            — POST /jobs/{id}/cancel — triggers cleanup and cancellation.
  human_input_provided  — POST /jobs/{id}/human-input — DB poll unblocks ExecuteScrapeMapActivity.

DETERMINISM RULES — all code in this module MUST follow:
  1. Use workflow.now()     — NOT datetime.now() or time.time()
  2. Use workflow.random()  — NOT random.random() or secrets.token_*
  3. Use workflow.uuid4()   — NOT uuid.uuid4()
  4. All I/O via activities — NO network, file, or DB calls in workflow code
  5. No threading, no locks, no mutable module-level state
"""

from __future__ import annotations

import datetime
from enum import StrEnum

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    import hashlib

    from open_banca_domain.entities.webhook_event import WebhookEventType
    from open_banca_orchestrator.activities.cleanup_sandbox import (
        CleanupSandboxInput,
        cleanup_sandbox,
    )
    from open_banca_orchestrator.activities.emit_webhook import (
        EmitWebhookInput,
        emit_webhook,
    )
    from open_banca_orchestrator.activities.execute_scrape_map import (
        ExecuteScrapeMapInput,
        ExecuteScrapeMapResult,
        execute_scrape_map,
    )
    from open_banca_orchestrator.activities.judge import JudgeInput, JudgeResult, judge
    from open_banca_orchestrator.activities.parse_excel import (
        ParseExcelInput,
        ParserConfig,
        TransactionRecord,
        parse_excel,
    )
    from open_banca_orchestrator.activities.persist_result import (
        PersistAccountInfo,
        PersistResultInput,
        persist_result,
    )
    from open_banca_orchestrator.activities.spawn_sandbox import (
        SpawnSandboxInput,
        SpawnSandboxResult,
        spawn_sandbox,
    )
    from open_banca_orchestrator.activities.validate import (
        ValidateInput,
        ValidateResult,
        validate,
    )


_RETRY_PARSE = RetryPolicy(maximum_attempts=1)

_RETRY_VALIDATE = RetryPolicy(
    initial_interval=datetime.timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_attempts=2,
)

_RETRY_WEBHOOK = RetryPolicy(
    initial_interval=datetime.timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=datetime.timedelta(hours=1),
    maximum_attempts=5,
)

_RETRY_JUDGE = RetryPolicy(maximum_attempts=1)

_RETRY_SANDBOX = RetryPolicy(
    initial_interval=datetime.timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_attempts=2,
)

_RETRY_CLEANUP = RetryPolicy(maximum_attempts=2)

_RETRY_PERSIST = RetryPolicy(
    initial_interval=datetime.timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_attempts=3,
)

_RETRY_EXECUTE_MAP = RetryPolicy(maximum_attempts=1)


class ScrapeMode(StrEnum):
    """Scrape mode passed by the API caller."""

    full_historical = "full_historical"
    incremental = "incremental"


class ScrapeJobInput(BaseModel):
    """Input for ScrapeJobWorkflow."""

    job_id: str = Field(description="Unique job identifier (idempotency key)")
    bank_id: str = Field(description="Bank to scrape (e.g. 'banco_general')")
    credential_ref: str = Field(
        description="Reference to encrypted credential in secrets store — never plaintext"
    )
    mode: ScrapeMode = Field(
        default=ScrapeMode.full_historical,
        description="Scrape mode: full_historical or incremental",
    )
    since_cursor: str | None = Field(
        default=None,
        description="ISO 8601 date string — start date for incremental scrape",
    )
    account_filter: list[str] | None = Field(
        default=None,
        description="Optional list of account IDs to scrape; None means all accounts",
    )


class AccountResult(BaseModel):
    """Per-account scrape result."""

    account_id: str
    transaction_count: int
    excel_path: str | None = None
    error: str | None = None


class ScrapeJobResult(BaseModel):
    """Output of ScrapeJobWorkflow."""

    job_id: str
    status: str = Field(
        description="completed | failed | cancelled | human_input_timeout | breakage"
    )
    accounts: list[AccountResult] = Field(default_factory=list)
    transactions: list[TransactionRecord] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


@workflow.defn(name="ScrapeJobWorkflow")
class ScrapeJobWorkflow:
    """Root workflow — one instance per POST /scrape request."""

    def __init__(self) -> None:
        self._otp_confirmed: bool = False
        self._remap_approved_proposal_id: str | None = None
        self._cancelled: bool = False
        self._cancel_reason: str = ""
        self._human_input_field_key: str | None = None
        self._human_input_answer: str | None = None
        self._human_input_persist: bool = True

    @workflow.signal(name="otp_confirmed")
    async def signal_otp_confirmed(self) -> None:
        """Legacy OTP signal — retained for API compatibility."""
        self._otp_confirmed = True

    @workflow.signal(name="remap_approved")
    async def signal_remap_approved(self, proposal_id: str) -> None:
        self._remap_approved_proposal_id = proposal_id

    @workflow.signal(name="cancel_job")
    async def signal_cancel_job(self, reason: str = "") -> None:
        self._cancelled = True
        self._cancel_reason = reason

    @workflow.signal(name="human_input_provided")
    async def signal_human_input_provided(
        self,
        field_key: str,
        answer: str,
        persist: bool = True,
    ) -> None:
        """Signal from API — activity polls DB; state kept for get_status."""
        self._human_input_field_key = field_key
        self._human_input_answer = answer
        self._human_input_persist = persist

    @workflow.query(name="get_status")
    def get_status(self) -> str:
        if self._cancelled:
            return "cancelled"
        if self._otp_confirmed:
            return "resumed"
        if self._human_input_answer is not None:
            return "resumed"
        return "running"

    @workflow.run
    async def run(self, input: ScrapeJobInput) -> ScrapeJobResult:  # noqa: A002, PLR0911
        all_transactions: list[TransactionRecord] = []
        account_results: list[AccountResult] = []
        sandbox_container_id = ""

        if self._cancelled:
            return ScrapeJobResult(
                job_id=input.job_id,
                status="cancelled",
                errors=[f"Cancelled before start: {self._cancel_reason}"],
            )

        sandbox_result: SpawnSandboxResult = await workflow.execute_activity(
            spawn_sandbox,
            SpawnSandboxInput(job_id=input.job_id, bank_id=input.bank_id),
            start_to_close_timeout=datetime.timedelta(seconds=60),
            retry_policy=_RETRY_SANDBOX,
        )
        sandbox_container_id = sandbox_result.container_id

        if self._cancelled:
            return await self._do_cancel(input.job_id, sandbox_container_id)

        scrape_result: ExecuteScrapeMapResult = await workflow.execute_activity(
            execute_scrape_map,
            ExecuteScrapeMapInput(
                job_id=input.job_id,
                bank_id=input.bank_id,
                credential_ref=input.credential_ref,
                sandbox_container_id=sandbox_container_id,
            ),
            start_to_close_timeout=datetime.timedelta(minutes=10),
            heartbeat_timeout=datetime.timedelta(seconds=30),
            retry_policy=_RETRY_EXECUTE_MAP,
        )

        if self._cancelled:
            return await self._do_cancel(input.job_id, sandbox_container_id)

        terminal = await self._handle_scrape_terminal(
            input,
            scrape_result,
            sandbox_container_id,
            all_transactions,
            account_results,
        )
        if terminal is not None:
            return terminal

        payload_hash = hashlib.sha256(
            str([t.model_dump() for t in all_transactions]).encode()
        ).hexdigest()

        validate_result: ValidateResult = await workflow.execute_activity(
            validate,
            ValidateInput(
                job_id=input.job_id,
                account_id="all",
                transactions=all_transactions,
                payload_hash=payload_hash,
            ),
            start_to_close_timeout=datetime.timedelta(seconds=60),
            retry_policy=_RETRY_VALIDATE,
        )

        if validate_result.breakage_detected:
            handled = await self._handle_validation_breakage(
                input.job_id,
                validate_result,
                sandbox_container_id,
            )
            if handled is not None:
                return handled

        await workflow.execute_activity(
            persist_result,
            PersistResultInput(
                job_id=input.job_id,
                bank_id=input.bank_id,
                accounts=[
                    PersistAccountInfo(
                        account_id=a.account_id,
                        transaction_count=a.transaction_count,
                    )
                    for a in account_results
                ],
                transactions=all_transactions,
            ),
            start_to_close_timeout=datetime.timedelta(seconds=30),
            retry_policy=_RETRY_PERSIST,
        )

        await workflow.execute_activity(
            emit_webhook,
            EmitWebhookInput(
                event_id=workflow.uuid4().hex,
                event_type=WebhookEventType.JOB_COMPLETED.value,
                job_id=input.job_id,
                payload={
                    "transaction_count": len(all_transactions),
                    "account_count": len(account_results),
                },
            ),
            start_to_close_timeout=datetime.timedelta(seconds=10),
            retry_policy=_RETRY_WEBHOOK,
        )

        await workflow.execute_activity(
            cleanup_sandbox,
            CleanupSandboxInput(container_id=sandbox_container_id),
            start_to_close_timeout=datetime.timedelta(seconds=30),
            retry_policy=_RETRY_CLEANUP,
        )

        return ScrapeJobResult(
            job_id=input.job_id,
            status="completed",
            accounts=account_results,
            transactions=all_transactions,
        )

    async def _handle_scrape_terminal(
        self,
        input: ScrapeJobInput,
        scrape_result: ExecuteScrapeMapResult,
        sandbox_container_id: str,
        all_transactions: list[TransactionRecord],
        account_results: list[AccountResult],
    ) -> ScrapeJobResult | None:
        """Handle non-success ExecuteScrapeMap statuses; return result or None to continue."""
        if scrape_result.status == "completed":
            if scrape_result.excel_path:
                account_id = (
                    input.account_filter[0]
                    if input.account_filter
                    else "default"
                )
                parse_result = await workflow.execute_activity(
                    parse_excel,
                    ParseExcelInput(
                        excel_path=scrape_result.excel_path,
                        content_hash=scrape_result.content_hash or "",
                        account_id=account_id,
                        parser_config=ParserConfig(bank_id=input.bank_id),
                    ),
                    start_to_close_timeout=datetime.timedelta(seconds=60),
                    retry_policy=_RETRY_PARSE,
                )
                all_transactions.extend(parse_result.transactions)
                account_results.append(
                    AccountResult(
                        account_id=account_id,
                        transaction_count=len(parse_result.transactions),
                        excel_path=scrape_result.excel_path,
                    )
                )
            return None

        await workflow.execute_activity(
            cleanup_sandbox,
            CleanupSandboxInput(container_id=sandbox_container_id),
            start_to_close_timeout=datetime.timedelta(seconds=30),
            retry_policy=_RETRY_CLEANUP,
        )

        if scrape_result.status == "human_input_timeout":
            await self._emit_failure(input.job_id, "human_input_timeout")
            return ScrapeJobResult(
                job_id=input.job_id,
                status="human_input_timeout",
                errors=scrape_result.errors or ["Human input not received in time"],
            )

        if scrape_result.status == "breakage":
            return await self._handle_scrape_breakage(
                input.job_id, scrape_result, sandbox_container_id
            )

        if scrape_result.status == "failed":
            await self._emit_failure(input.job_id, "scrape_failed")
            return ScrapeJobResult(
                job_id=input.job_id,
                status="failed",
                errors=scrape_result.errors or ["ExecuteScrapeMapActivity failed"],
            )

        await self._emit_failure(input.job_id, f"unknown_status_{scrape_result.status}")
        return ScrapeJobResult(
            job_id=input.job_id,
            status="failed",
            errors=scrape_result.errors or [f"Unexpected status: {scrape_result.status}"],
        )

    async def _handle_scrape_breakage(
        self,
        job_id: str,
        scrape_result: ExecuteScrapeMapResult,
        sandbox_container_id: str,
    ) -> ScrapeJobResult:
        from open_banca_domain.entities.breakage_event import (  # noqa: PLC0415
            BreakageEvent as DomainBreakageEvent,
        )

        breakage_hash = hashlib.sha256(
            str(scrape_result.errors).encode()
        ).hexdigest()
        breakage_event = DomainBreakageEvent(
            job_id=job_id,
            step_index=scrape_result.steps_completed,
            step_type="scrape_map",
            error_class=scrape_result.errors[0] if scrape_result.errors else "breakage",
            screenshot_ref="sha256:none",
            dom_excerpt=str(scrape_result.errors)[:10240],
            occurred_at=workflow.now(),
        )

        judge_result: JudgeResult = await workflow.execute_activity(
            judge,
            JudgeInput(
                job_id=job_id,
                breakage_event=breakage_event,
                breakage_hash=breakage_hash,
            ),
            start_to_close_timeout=datetime.timedelta(seconds=30),
            retry_policy=_RETRY_JUDGE,
        )

        await workflow.execute_activity(
            emit_webhook,
            EmitWebhookInput(
                event_id=workflow.uuid4().hex,
                event_type=WebhookEventType.JOB_REMAP_PROPOSED.value,
                job_id=job_id,
                payload={
                    "route": judge_result.route,
                    "confidence": judge_result.confidence,
                    "risk": judge_result.risk,
                    "rationale": judge_result.rationale,
                },
            ),
            start_to_close_timeout=datetime.timedelta(seconds=10),
            retry_policy=_RETRY_WEBHOOK,
        )

        await workflow.wait_condition(
            lambda: self._remap_approved_proposal_id is not None or self._cancelled,
        )

        if self._cancelled:
            return await self._do_cancel(job_id, sandbox_container_id)

        return ScrapeJobResult(
            job_id=job_id,
            status="breakage",
            errors=scrape_result.errors,
        )

    async def _handle_validation_breakage(
        self,
        job_id: str,
        validate_result: ValidateResult,
        sandbox_container_id: str,
    ) -> ScrapeJobResult | None:
        from open_banca_domain.entities.breakage_event import (  # noqa: PLC0415
            BreakageEvent as DomainBreakageEvent,
        )

        breakage_hash = hashlib.sha256(str(validate_result.issues).encode()).hexdigest()
        breakage_event = DomainBreakageEvent(
            job_id=job_id,
            step_index=-1,
            step_type="validate",
            error_class="validation_failure",
            screenshot_ref="sha256:none",
            dom_excerpt=str(validate_result.issues)[:10240],
            occurred_at=workflow.now(),
        )

        judge_result: JudgeResult = await workflow.execute_activity(
            judge,
            JudgeInput(
                job_id=job_id,
                breakage_event=breakage_event,
                breakage_hash=breakage_hash,
            ),
            start_to_close_timeout=datetime.timedelta(seconds=30),
            retry_policy=_RETRY_JUDGE,
        )

        await workflow.execute_activity(
            emit_webhook,
            EmitWebhookInput(
                event_id=workflow.uuid4().hex,
                event_type=WebhookEventType.JOB_REMAP_PROPOSED.value,
                job_id=job_id,
                payload={
                    "route": judge_result.route,
                    "confidence": judge_result.confidence,
                    "risk": judge_result.risk,
                    "rationale": judge_result.rationale,
                },
            ),
            start_to_close_timeout=datetime.timedelta(seconds=10),
            retry_policy=_RETRY_WEBHOOK,
        )

        await workflow.wait_condition(
            lambda: self._remap_approved_proposal_id is not None or self._cancelled,
        )

        if self._cancelled:
            return await self._do_cancel(job_id, sandbox_container_id)

        return None

    async def _do_cancel(self, job_id: str, sandbox_container_id: str = "") -> ScrapeJobResult:
        if sandbox_container_id:
            await workflow.execute_activity(
                cleanup_sandbox,
                CleanupSandboxInput(container_id=sandbox_container_id),
                start_to_close_timeout=datetime.timedelta(seconds=30),
                retry_policy=_RETRY_CLEANUP,
            )
        await workflow.execute_activity(
            emit_webhook,
            EmitWebhookInput(
                event_id=workflow.uuid4().hex,
                event_type=WebhookEventType.JOB_CANCELLED.value,
                job_id=job_id,
                payload={"reason": self._cancel_reason},
            ),
            start_to_close_timeout=datetime.timedelta(seconds=10),
            retry_policy=_RETRY_WEBHOOK,
        )
        return ScrapeJobResult(
            job_id=job_id,
            status="cancelled",
            errors=[f"Cancelled: {self._cancel_reason}"],
        )

    async def _emit_failure(self, job_id: str, reason: str) -> None:
        await workflow.execute_activity(
            emit_webhook,
            EmitWebhookInput(
                event_id=workflow.uuid4().hex,
                event_type=WebhookEventType.JOB_FAILED.value,
                job_id=job_id,
                payload={"reason": reason},
            ),
            start_to_close_timeout=datetime.timedelta(seconds=10),
            retry_policy=_RETRY_WEBHOOK,
        )
