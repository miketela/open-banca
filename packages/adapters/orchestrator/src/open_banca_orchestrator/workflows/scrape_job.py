"""ScrapeJobWorkflow — root workflow for each POST /scrape request.

Orchestrates the full scraping pipeline per docs/02-components/orchestrator.md.

Signals:
  otp_confirmed  — POST /jobs/{id}/otp-confirmed — unblocks OTP wait.
  remap_approved — POST /maps/{bank}/proposals/{id}/approve — resumes after remap.
  cancel_job     — POST /jobs/{id}/cancel — triggers cleanup and cancellation.

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
    from open_banca_orchestrator.activities.emit_webhook import (
        EmitWebhookInput,
        WebhookEvent,
        WebhookEventType,
        emit_webhook,
    )
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
    from open_banca_orchestrator.activities.parse_excel import (
        ParseExcelInput,
        ParserConfig,
        TransactionRecord,
        parse_excel,
    )
    from open_banca_orchestrator.activities.validate import ValidateInput, validate


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

_RETRY_WEBHOOK = RetryPolicy(
    initial_interval=datetime.timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=datetime.timedelta(hours=1),
    maximum_attempts=5,
)

_RETRY_NONE = RetryPolicy(maximum_attempts=1)  # MapperAgent, RemapperAgent


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
        return "running"

    # -----------------------------------------------------------------------
    # Main run
    # -----------------------------------------------------------------------

    @workflow.run
    async def run(self, input: ScrapeJobInput) -> ScrapeJobResult:  # noqa: A002
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
        # Step 2: TODO(task-sandbox) — spawn sandbox Docker container per job
        # ------------------------------------------------------------------
        # sandbox_result = await workflow.execute_activity(
        #     spawn_sandbox,
        #     SpawnSandboxInput(job_id=input.job_id),
        #     start_to_close_timeout=timedelta(seconds=60),
        # )
        # sandbox_container_id = sandbox_result.container_id

        # Placeholder until sandbox task lands
        sandbox_container_id = f"sandbox-{input.job_id}"  # replaced by task-sandbox

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
                EmitWebhookInput(
                    event=WebhookEvent(
                        event_id=workflow.uuid4().hex,
                        event_type=WebhookEventType.job_otp_required,
                        job_id=input.job_id,
                        timestamp=workflow.now().isoformat(),
                        payload={"bank_id": input.bank_id},
                    )
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
                    EmitWebhookInput(
                        event=WebhookEvent(
                            event_id=workflow.uuid4().hex,
                            event_type=WebhookEventType.job_failed,
                            job_id=input.job_id,
                            timestamp=workflow.now().isoformat(),
                            payload={"reason": "otp_timeout"},
                        )
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
                return await self._do_cancel(input.job_id)

        elif login_result.status == LoginStatus.failed:
            errors.append(f"Login failed: {login_result.error_detail}")
            await self._emit_failure(input.job_id, "login_failed")
            return ScrapeJobResult(
                job_id=input.job_id,
                status="failed",
                errors=errors,
            )

        # ------------------------------------------------------------------
        # Steps 5-7: Navigate → Download → Parse per account
        # ------------------------------------------------------------------
        # Determine account list (placeholder — real account list from map.json/bank)
        # TODO: replace with actual accounts from map.json + account_filter
        accounts_to_scrape: list[str] = input.account_filter or ["default-account"]

        since_date = (
            datetime.date.fromisoformat(input.since_cursor)
            if input.since_cursor
            else datetime.date(2020, 1, 1)
        )
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
            return await self._do_cancel(input.job_id)

        # ------------------------------------------------------------------
        # Step 8: ValidateActivity
        # ------------------------------------------------------------------
        payload_hash = hashlib.sha256(
            str([t.model_dump() for t in all_transactions]).encode()
        ).hexdigest()

        await workflow.execute_activity(
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
        # Step 9: TODO(task-storage) — persist job/accounts/transactions
        # ------------------------------------------------------------------
        # await workflow.execute_activity(
        #     persist_result,
        #     PersistResultInput(job_id=input.job_id, accounts=account_results,
        #                        transactions=all_transactions),
        #     start_to_close_timeout=timedelta(seconds=30),
        # )

        # ------------------------------------------------------------------
        # Step 10: EmitWebhookActivity — job.completed
        # ------------------------------------------------------------------
        await workflow.execute_activity(
            emit_webhook,
            EmitWebhookInput(
                event=WebhookEvent(
                    event_id=workflow.uuid4().hex,
                    event_type=WebhookEventType.job_completed,
                    job_id=input.job_id,
                    timestamp=workflow.now().isoformat(),
                    payload={
                        "transaction_count": len(all_transactions),
                        "account_count": len(account_results),
                    },
                )
            ),
            start_to_close_timeout=datetime.timedelta(seconds=10),
            retry_policy=_RETRY_WEBHOOK,
        )

        # ------------------------------------------------------------------
        # Step 11: TODO(task-sandbox) — cleanup sandbox container
        # ------------------------------------------------------------------
        # await workflow.execute_activity(
        #     cleanup_sandbox,
        #     CleanupInput(sandbox_container_id=sandbox_container_id),
        #     start_to_close_timeout=timedelta(seconds=30),
        # )

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

    async def _do_cancel(self, job_id: str) -> ScrapeJobResult:
        """Execute cleanup and return a cancelled result."""
        # TODO(task-sandbox): kill sandbox container on cancel
        await workflow.execute_activity(
            emit_webhook,
            EmitWebhookInput(
                event=WebhookEvent(
                    event_id=workflow.uuid4().hex,
                    event_type=WebhookEventType.job_cancelled,
                    job_id=job_id,
                    timestamp=workflow.now().isoformat(),
                    payload={"reason": self._cancel_reason},
                )
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
        """Emit a job.failed webhook event."""
        await workflow.execute_activity(
            emit_webhook,
            EmitWebhookInput(
                event=WebhookEvent(
                    event_id=workflow.uuid4().hex,
                    event_type=WebhookEventType.job_failed,
                    job_id=job_id,
                    timestamp=workflow.now().isoformat(),
                    payload={"reason": reason},
                )
            ),
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
