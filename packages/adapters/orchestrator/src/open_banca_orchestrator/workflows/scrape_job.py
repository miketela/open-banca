"""ScrapeJobWorkflow — root workflow for each POST /scrape request.

Orchestrates the full scraping pipeline per docs/02-components/orchestrator.md.

Signals:
  otp_confirmed         — POST /jobs/{id}/otp-confirmed — unblocks OTP wait.
  remap_approved        — POST /maps/{bank}/proposals/{id}/approve — resumes after remap.
  cancel_job            — POST /jobs/{id}/cancel — triggers cleanup and cancellation.
  human_input_provided  — POST /jobs/{id}/human-input — delivers answer for prompt_user step.

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
from temporalio.workflow import ActivityHandle

with workflow.unsafe.imports_passed_through():
    import hashlib

    from open_banca_orchestrator.activities.download_excel import (
        DownloadExcelInput,
        DownloadPeriod,
        download_excel,
    )
    from open_banca_domain.entities.webhook_event import WebhookEventType
    from open_banca_orchestrator.activities.emit_webhook import EmitWebhookInput, emit_webhook
    from open_banca_orchestrator.activities.judge import JudgeInput, JudgeResult, judge
    from open_banca_orchestrator.activities.login import (
        LoginInput,
        LoginResult,
        LoginStatus,
        login,
    )
    from open_banca_orchestrator.activities.navigate import NavigateInput, navigate
    from open_banca_orchestrator.activities.otp_signal_await import (
        OTPSignalAwaitInput,
        otp_signal_await,
    )
    from open_banca_orchestrator.activities.human_input_await import (
        HumanInputAwaitInput,
        HumanInputAwaitResult,
        human_input_await,
    )
    from open_banca_orchestrator.activities.parse_excel import (
        ParseExcelInput,
        ParserConfig,
        TransactionRecord,
        parse_excel,
    )
    from open_banca_orchestrator.activities.validate import ValidateInput, ValidateResult, validate
    from open_banca_orchestrator.activities.spawn_sandbox import (
        SpawnSandboxInput,
        SpawnSandboxResult,
        spawn_sandbox,
    )
    from open_banca_orchestrator.activities.cleanup_sandbox import (
        CleanupSandboxInput,
        cleanup_sandbox,
    )
    from open_banca_orchestrator.activities.persist_result import (
        PersistAccountInfo,
        PersistResultInput,
        persist_result,
    )
    from open_banca_orchestrator.activities.list_accounts import (
        ListAccountsInput,
        ListAccountsResult,
        list_accounts,
    )


# ---------------------------------------------------------------------------
# Retry policies per activity (orchestrator.md §Inventario)
# ---------------------------------------------------------------------------

_RETRY_LOGIN = RetryPolicy(
    initial_interval=datetime.timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=datetime.timedelta(seconds=30),
    maximum_attempts=2,
)

_RETRY_NAVIGATE = RetryPolicy(
    initial_interval=datetime.timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=datetime.timedelta(seconds=30),
    maximum_attempts=3,
)

_RETRY_DOWNLOAD = RetryPolicy(
    initial_interval=datetime.timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=datetime.timedelta(minutes=1),
    maximum_attempts=3,
)

_RETRY_PARSE = RetryPolicy(maximum_attempts=1)

_RETRY_VALIDATE = RetryPolicy(
    initial_interval=datetime.timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_attempts=2,
)

_RETRY_OTP = RetryPolicy(maximum_attempts=1)  # no retry — hard cap via timeout
_RETRY_HUMAN_INPUT = RetryPolicy(maximum_attempts=1)  # no retry — hard cap via timeout_s

_RETRY_WEBHOOK = RetryPolicy(
    initial_interval=datetime.timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=datetime.timedelta(hours=1),
    maximum_attempts=5,
)

_RETRY_NONE = RetryPolicy(maximum_attempts=1)  # MapperAgent, RemapperAgent

_RETRY_JUDGE = RetryPolicy(maximum_attempts=1)  # Judge: idempotent on breakage hash

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

_RETRY_LIST_ACCOUNTS = RetryPolicy(maximum_attempts=2)


# ---------------------------------------------------------------------------
# I/O types
# ---------------------------------------------------------------------------


class ScrapeMode(StrEnum):
    """Scrape mode passed by the API caller."""

    full_historical = "full_historical"
    incremental = "incremental"


class ScrapeJobInput(BaseModel):
    """Input for ScrapeJobWorkflow.

    Passed as the single argument to workflow.run().
    """

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
    status: str = Field(description="completed | failed | cancelled | otp_timeout")
    accounts: list[AccountResult] = Field(default_factory=list)
    transactions: list[TransactionRecord] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


@workflow.defn(name="ScrapeJobWorkflow")
class ScrapeJobWorkflow:
    """Root workflow — one instance per POST /scrape request.

    State machine (orchestrator.md §Estados del job):
      pending → running → otp_required → resumed → completed | failed
      running → escalated → running | failed
      any → cancelled (via cancel_job signal)
    """

    def __init__(self) -> None:
        # Signal state variables — set by signal handlers, read via wait_condition
        self._otp_confirmed: bool = False
        self._remap_approved_proposal_id: str | None = None
        self._cancelled: bool = False
        self._cancel_reason: str = ""
        # human_input_provided signal state (ADR-0021)
        self._human_input_field_key: str | None = None
        self._human_input_answer: str | None = None
        self._human_input_persist: bool = True

    # -----------------------------------------------------------------------
    # Signal handlers
    # -----------------------------------------------------------------------

    @workflow.signal(name="otp_confirmed")
    async def signal_otp_confirmed(self) -> None:
        """Signal: POST /jobs/{id}/otp-confirmed.

        Unblocks the workflow.wait_condition() in the OTP wait section.
        Triggered by the API after the user approves the Clave Móvil push.
        """
        self._otp_confirmed = True

    @workflow.signal(name="remap_approved")
    async def signal_remap_approved(self, proposal_id: str) -> None:
        """Signal: POST /maps/{bank}/proposals/{id}/approve.

        Resumes the job after a remap_proposed event has been operator-approved.
        Sets the proposal_id so the workflow can pass it to RemapBankWorkflow.

        Args:
            proposal_id: The proposal ID from JudgeActivity to pass to RemapBankWorkflow.
        """
        self._remap_approved_proposal_id = proposal_id

    @workflow.signal(name="cancel_job")
    async def signal_cancel_job(self, reason: str = "") -> None:
        """Signal: POST /jobs/{id}/cancel.

        Triggers cleanup and marks the job as cancelled.

        Args:
            reason: Human-readable cancellation reason (optional).
        """
        self._cancelled = True
        self._cancel_reason = reason

    @workflow.signal(name="human_input_provided")
    async def signal_human_input_provided(
        self,
        field_key: str,
        answer: str,
        persist: bool = True,
    ) -> None:
        """Signal: POST /jobs/{id}/human-input.

        Unblocks the workflow.wait_condition() in the human-input wait section.
        Carries the answer payload — distinct from otp_confirmed which is payload-less.

        Args:
            field_key: Matches the field_key in the pending prompt_user step.
            answer: The plaintext answer provided by the operator.
            persist: Whether to cache the answer in the security_q vault namespace.
        """
        self._human_input_field_key = field_key
        self._human_input_answer = answer
        self._human_input_persist = persist

    # -----------------------------------------------------------------------
    # Queries
    # -----------------------------------------------------------------------

    @workflow.query(name="get_status")
    def get_status(self) -> str:
        """Return the current job status as a string.

        Read-only — does not modify workflow state.
        """
        if self._cancelled:
            return "cancelled"
        if self._otp_confirmed:
            return "resumed"
        if self._human_input_answer is not None:
            return "resumed"
        return "running"

    # -----------------------------------------------------------------------
    # Main run
    # -----------------------------------------------------------------------

    @workflow.run
    async def run(self, input: ScrapeJobInput) -> ScrapeJobResult:  # noqa: A002, PLR0911
        """Orchestrate the full scraping pipeline.

        Sequential steps per orchestrator.md §Topología:
          1. Check if map.json exists; if not, spawn MapBankWorkflow child.
          2. TODO(task-sandbox): spawn sandbox container per job.
          3. LoginActivity — authenticate, detect OTP requirement.
          4. If needs_otp: wait for otp_confirmed signal (4 min timeout).
          5. NavigateActivity per account → navigate to transaction page.
          6. DownloadExcelActivity per account → download Excel export.
          7. ParseExcelActivity per file → list of TransactionRecord.
          8. ValidateActivity → validate normalized payload.
          9. TODO(task-storage): persist results (job/accounts/transactions).
          10. EmitWebhookActivity → job.completed event.
          11. TODO(task-sandbox): cleanup sandbox container.
        """
        errors: list[str] = []
        all_transactions: list[TransactionRecord] = []
        account_results: list[AccountResult] = []

        # Early exit if cancelled before we even start
        if self._cancelled:
            return ScrapeJobResult(
                job_id=input.job_id,
                status="cancelled",
                errors=[f"Cancelled before start: {self._cancel_reason}"],
            )

        # ------------------------------------------------------------------
        # Step 1: Ensure map.json exists — spawn MapBankWorkflow if needed
        # ------------------------------------------------------------------
        # TODO: check storage for existing map.json for bank_id.
        # For now, always assume we need to spawn the child workflow.
        # When task 14 lands, replace this TODO with actual storage check.

        # Example of how child workflow will be spawned (skeleton — not executed):
        # map_result = await workflow.execute_child_workflow(
        #     MapBankWorkflow.run,
        #     MapBankInput(
        #         bank_id=input.bank_id,
        #         job_id=input.job_id,
        #     ),
        #     id=f"map-{input.bank_id}-{workflow.now().strftime('%Y%m%d')}",
        #     task_queue=workflow.info().task_queue,
        # )

        # ------------------------------------------------------------------
        # Step 2: Spawn sandbox Docker container per job
        # ------------------------------------------------------------------
        sandbox_result: SpawnSandboxResult = await workflow.execute_activity(
            spawn_sandbox,
            SpawnSandboxInput(job_id=input.job_id, bank_id=input.bank_id),
            start_to_close_timeout=datetime.timedelta(seconds=60),
            retry_policy=_RETRY_SANDBOX,
        )
        sandbox_container_id = sandbox_result.container_id

        # ------------------------------------------------------------------
        # Step 3: LoginActivity — authenticate to bank portal
        # ------------------------------------------------------------------
        login_nonce = workflow.uuid4().hex
        login_result: LoginResult = await workflow.execute_activity(
            login,
            LoginInput(
                job_id=input.job_id,
                bank_id=input.bank_id,
                credential_ref=input.credential_ref,
                nonce=login_nonce,
                sandbox_container_id=sandbox_container_id,
            ),
            start_to_close_timeout=datetime.timedelta(seconds=90),
            heartbeat_timeout=datetime.timedelta(seconds=15),
            retry_policy=_RETRY_LOGIN,
        )

        # ------------------------------------------------------------------
        # Step 4: OTP handling
        # ------------------------------------------------------------------
        if login_result.status == LoginStatus.needs_otp:
            # Emit job.otp_required webhook so the client can prompt the user
            await workflow.execute_activity(
                emit_webhook,
                self._webhook_input(
                    WebhookEventType.JOB_OTP_REQUIRED,
                    input.job_id,
                    {"bank_id": input.bank_id},
                ),
                start_to_close_timeout=datetime.timedelta(seconds=10),
                retry_policy=_RETRY_WEBHOOK,
            )

            # Start the sidecar keepalive activity concurrently.
            # It runs until cancelled (signal arrived) or times out (4 min).
            # We do NOT await it here — it is scheduled and cancelled after the
            # wait_condition resolves.
            otp_keepalive_handle: ActivityHandle | None = None
            if login_result.browser_session_token is not None:
                otp_keepalive_handle = workflow.start_activity(
                    otp_signal_await,
                    OTPSignalAwaitInput(
                        job_id=input.job_id,
                        browser_session_token=login_result.browser_session_token,
                    ),
                    start_to_close_timeout=datetime.timedelta(minutes=4),
                    retry_policy=_RETRY_OTP,
                )

            # Deterministic signal wait: blocks until otp_confirmed signal arrives
            # or 4-minute hard cap fires.
            try:
                await workflow.wait_condition(
                    lambda: self._otp_confirmed or self._cancelled,
                    timeout=datetime.timedelta(minutes=4),
                )
            except TimeoutError:
                # OTP hard cap exceeded — emit failure webhook and return
                if otp_keepalive_handle is not None:
                    otp_keepalive_handle.cancel()
                await workflow.execute_activity(
                    emit_webhook,
                    self._webhook_input(
                        WebhookEventType.JOB_FAILED,
                        input.job_id,
                        {"reason": "otp_timeout"},
                    ),
                    start_to_close_timeout=datetime.timedelta(seconds=10),
                    retry_policy=_RETRY_WEBHOOK,
                )
                return ScrapeJobResult(
                    job_id=input.job_id,
                    status="otp_timeout",
                    errors=["OTP confirmation not received within 4 minutes"],
                )
            finally:
                if otp_keepalive_handle is not None:
                    otp_keepalive_handle.cancel()

            if self._cancelled:
                return await self._do_cancel(input.job_id, sandbox_container_id)

        elif login_result.status == LoginStatus.failed:
            errors.append(f"Login failed: {login_result.error_detail}")
            await workflow.execute_activity(
                cleanup_sandbox,
                CleanupSandboxInput(container_id=sandbox_container_id),
                start_to_close_timeout=datetime.timedelta(seconds=30),
                retry_policy=_RETRY_CLEANUP,
            )
            await self._emit_failure(input.job_id, "login_failed")
            return ScrapeJobResult(
                job_id=input.job_id,
                status="failed",
                errors=errors,
            )

        # ------------------------------------------------------------------
        # Steps 5-7: Navigate → Download → Parse per account
        # ------------------------------------------------------------------
        if input.account_filter:
            accounts_to_scrape: list[str] = input.account_filter
        else:
            acct_result: ListAccountsResult = await workflow.execute_activity(
                list_accounts,
                ListAccountsInput(
                    bank_id=input.bank_id,
                    browser_session_token=login_result.browser_session_token.container_id
                    if login_result.browser_session_token
                    else None,
                ),
                start_to_close_timeout=datetime.timedelta(seconds=30),
                retry_policy=_RETRY_LIST_ACCOUNTS,
            )
            accounts_to_scrape = [a.account_id for a in acct_result.accounts]

        # Resolve since_date:
        #   - full_historical mode → 6-month lookback (configurable via env in activity)
        #   - incremental mode with cursor → use cursor as-is (NO additional subtraction;
        #     DedupEngine.effective_since() already applied the 3-day buffer at API layer)
        #   - incremental mode without cursor → should not reach here (API falls back to
        #     full_historical when no cursor exists), but guard defensively
        if input.mode == ScrapeMode.incremental and input.since_cursor is not None:
            since_date = datetime.date.fromisoformat(input.since_cursor)
        else:
            # Full historical: default 6-month lookback
            since_date = (workflow.now() - datetime.timedelta(days=180)).date()
        until_date = workflow.now().date()

        for account_id in accounts_to_scrape:
            if self._cancelled:
                break

            step_id = f"{input.job_id}-nav-{account_id}"

            # Navigate
            await workflow.execute_activity(
                navigate,
                NavigateInput(
                    job_id=input.job_id,
                    step_id=step_id,
                    bank_id=input.bank_id,
                    account_id=account_id,
                    browser_session_token=login_result.browser_session_token
                    or _placeholder_session_token(),
                ),
                start_to_close_timeout=datetime.timedelta(seconds=30),
                heartbeat_timeout=datetime.timedelta(seconds=10),
                retry_policy=_RETRY_NAVIGATE,
            )

            # Download
            download_result = await workflow.execute_activity(
                download_excel,
                DownloadExcelInput(
                    job_id=input.job_id,
                    account_id=account_id,
                    period=DownloadPeriod(since=since_date, until=until_date),
                    bank_id=input.bank_id,
                    browser_session_token=login_result.browser_session_token
                    or _placeholder_session_token(),
                ),
                start_to_close_timeout=datetime.timedelta(minutes=2),
                heartbeat_timeout=datetime.timedelta(seconds=15),
                retry_policy=_RETRY_DOWNLOAD,
            )

            # Parse (sync threadpool activity)
            parse_result = await workflow.execute_activity(
                parse_excel,
                ParseExcelInput(
                    excel_path=download_result.excel_path,
                    content_hash=download_result.content_hash,
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
                    excel_path=download_result.excel_path,
                )
            )

        if self._cancelled:
            return await self._do_cancel(input.job_id, sandbox_container_id)

        # ------------------------------------------------------------------
        # Step 8: ValidateActivity
        # ------------------------------------------------------------------
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

        # ------------------------------------------------------------------
        # Step 8b: JudgeActivity — triggered when validation detects breakage
        # v1 routing (ADR-0013 amendment): ALWAYS human_required.
        # Emit job.remap_proposed webhook then wait for remap_approved signal.
        # ------------------------------------------------------------------
        if validate_result.breakage_detected:
            breakage_hash = hashlib.sha256(
                str(validate_result.issues).encode()
            ).hexdigest()

            # Build a synthetic BreakageEvent from validation failures
            from open_banca_domain.entities.breakage_event import (  # noqa: PLC0415
                BreakageEvent as DomainBreakageEvent,
            )

            breakage_event = DomainBreakageEvent(
                job_id=input.job_id,
                step_index=-1,  # validation step (post-parse)
                step_type="validate",
                error_class="validation_failure",
                screenshot_ref="sha256:none",
                dom_excerpt=str(validate_result.issues)[:10240],
                occurred_at=workflow.now(),
            )

            judge_result: JudgeResult = await workflow.execute_activity(
                judge,
                JudgeInput(
                    job_id=input.job_id,
                    breakage_event=breakage_event,
                    breakage_hash=breakage_hash,
                ),
                start_to_close_timeout=datetime.timedelta(seconds=30),
                retry_policy=_RETRY_JUDGE,
            )

            # v1: route is always human_required — emit webhook and wait for approval
            # (In v2, auto-apply path would branch here on route/confidence/risk.)
            await workflow.execute_activity(
                emit_webhook,
                self._webhook_input(
                    WebhookEventType.JOB_REMAP_PROPOSED,
                    input.job_id,
                    {
                        "route": judge_result.route,
                        "confidence": judge_result.confidence,
                        "risk": judge_result.risk,
                        "rationale": judge_result.rationale,
                    },
                ),
                start_to_close_timeout=datetime.timedelta(seconds=10),
                retry_policy=_RETRY_WEBHOOK,
            )

            # Wait for operator remap_approved signal (no hard timeout — operator-driven)
            await workflow.wait_condition(
                lambda: self._remap_approved_proposal_id is not None or self._cancelled,
            )

            if self._cancelled:
                return await self._do_cancel(input.job_id, sandbox_container_id)

        # ------------------------------------------------------------------
        # Step 9: Persist job/accounts/transactions to storage
        # ------------------------------------------------------------------
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

        # ------------------------------------------------------------------
        # Step 10: EmitWebhookActivity — job.completed
        # ------------------------------------------------------------------
        await workflow.execute_activity(
            emit_webhook,
            self._webhook_input(
                WebhookEventType.JOB_COMPLETED,
                input.job_id,
                {
                    "transaction_count": len(all_transactions),
                    "account_count": len(account_results),
                },
            ),
            start_to_close_timeout=datetime.timedelta(seconds=10),
            retry_policy=_RETRY_WEBHOOK,
        )

        # ------------------------------------------------------------------
        # Step 11: Cleanup sandbox container
        # ------------------------------------------------------------------
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
            errors=errors,
        )

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    async def _do_cancel(self, job_id: str, sandbox_container_id: str = "") -> ScrapeJobResult:
        """Execute cleanup and return a cancelled result."""
        if sandbox_container_id:
            await workflow.execute_activity(
                cleanup_sandbox,
                CleanupSandboxInput(container_id=sandbox_container_id),
                start_to_close_timeout=datetime.timedelta(seconds=30),
                retry_policy=_RETRY_CLEANUP,
            )
        await workflow.execute_activity(
            emit_webhook,
            self._webhook_input(
                WebhookEventType.JOB_FAILED,
                job_id,
                {"reason": "cancelled", "cancel_reason": self._cancel_reason},
            ),
            start_to_close_timeout=datetime.timedelta(seconds=10),
            retry_policy=_RETRY_WEBHOOK,
        )
        return ScrapeJobResult(
            job_id=job_id,
            status="cancelled",
            errors=[f"Cancelled: {self._cancel_reason}"],
        )

    def _webhook_input(
        self,
        event_type: WebhookEventType,
        job_id: str,
        payload: dict[str, object],
    ) -> EmitWebhookInput:
        """Build EmitWebhookInput with a deterministic event_id."""
        return EmitWebhookInput(
            event_id=workflow.uuid4().hex,
            event_type=event_type.value,
            job_id=job_id,
            payload=payload,
        )

    async def _emit_failure(self, job_id: str, reason: str) -> None:
        """Emit a job.failed webhook event."""
        await workflow.execute_activity(
            emit_webhook,
            self._webhook_input(WebhookEventType.JOB_FAILED, job_id, {"reason": reason}),
            start_to_close_timeout=datetime.timedelta(seconds=10),
            retry_policy=_RETRY_WEBHOOK,
        )


def _placeholder_session_token():  # type: ignore[return]
    """Return a placeholder BrowserSessionToken when login didn't return one.

    Used only in code paths where status==success (no sidecar needed).
    Will be removed when LoginActivity is fully implemented.
    """
    from open_banca_orchestrator.activities.login import BrowserSessionToken  # noqa: PLC0415

    return BrowserSessionToken(
        container_id="placeholder",
        socket_path="/run/banca/sidecar.sock",
        sidecar_pid=0,
    )
