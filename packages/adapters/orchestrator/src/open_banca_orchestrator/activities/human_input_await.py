"""HumanInputAwaitActivity — long-running keepalive during human-input wait (ADR-0021).

Analogous to OTPSignalAwaitActivity but for the ``prompt_user`` step type.

This activity's dual purpose:
  1. Keep the BrowserSidecar connection alive via Temporal heartbeats while the
     workflow waits for the ``human_input_provided`` signal.
  2. Emit the ``job.human_input_required`` webhook so the operator / client knows
     to call POST /jobs/{id}/human-input.

Key differences from OTPSignalAwaitActivity:
  - Signal carries payload: {field_key, answer, persist}.
  - Timeout is configurable per-step (``timeout_s``, default 240 s).
  - Cache TTL 90 days; OTP has no cache.

Retry policy: NO retry (non_retryable).
Start-to-close timeout: equal to ``timeout_s`` + 30 s buffer.
Heartbeat every 15 s.

IMPLEMENTATION STATUS: skeleton.
Sidecar IPC (BrowserSidecar ADR-0019) is not yet wired — heartbeat loop raises
NotImplementedError for the ping itself, but activity structure is complete.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from temporalio import activity

from open_banca_orchestrator.activities.login import BrowserSessionToken

logger = logging.getLogger(__name__)


class HumanInputAwaitInput(BaseModel):
    """Input for HumanInputAwaitActivity."""

    job_id: str = Field(description="Unique job identifier")
    field_key: str = Field(description="Stable field key for this prompt_user step")
    question_hash: str = Field(description="SHA-256 hex cache key for this question")
    question_text: str = Field(description="Raw question text extracted from DOM (for webhook payload)")
    selector: str = Field(description="CSS/XPath selector for the answer input field")
    timeout_s: int = Field(default=240, description="Seconds to wait before hard timeout")
    browser_session_token: BrowserSessionToken | None = Field(
        default=None,
        description="Token from LoginActivity for sidecar keepalive (ADR-0019)",
    )
    heartbeat_interval_s: int = Field(
        default=15,
        description="Seconds between heartbeat pings to the BrowserSidecar",
    )


class HumanInputAwaitResult(BaseModel):
    """Result from HumanInputAwaitActivity."""

    sidecar_alive: bool = Field(
        default=True,
        description="True if sidecar was still alive when activity completed",
    )
    heartbeat_count: int = Field(
        default=0,
        description="Number of successful heartbeat pings sent during the wait",
    )


class HumanInputAwaitActivity:
    """HumanInputAwaitActivity class-based wrapper (kept for compatibility)."""


@activity.defn(name="HumanInputAwaitActivity")
async def human_input_await(input: HumanInputAwaitInput) -> HumanInputAwaitResult:  # noqa: A002
    """Emit job.human_input_required webhook and heartbeat while waiting for signal.

    Loop:
      1. Emit webhook ``job.human_input_required`` on first iteration.
      2. Ping BrowserSidecar via Unix socket (ADR-0019 — TODO).
      3. Call ``activity.heartbeat()`` to keep Temporal alive.
      4. Sleep ``heartbeat_interval_s``.
      5. Exit when activity is cancelled (signal arrived or workflow cancelled)
         or start-to-close timeout fires.

    On sidecar failure: raise ApplicationError(non_retryable=True, type=...).

    TODO: implement sidecar IPC ping via Unix socket (ADR-0019).
    TODO: call activity.heartbeat(sidecar_alive=True, ping_count=N) each iteration.
    TODO: integrate real webhook dispatch via WebhookDispatcher.
    """
    from temporalio.exceptions import ApplicationError

    heartbeat_count = 0
    webhook_emitted = False
    expires_at = datetime.fromtimestamp(
        activity.info().scheduled_time.timestamp() + input.timeout_s, tz=UTC
    ).isoformat()

    while True:
        # Emit webhook on first iteration
        if not webhook_emitted:
            try:
                _emit_human_input_required_webhook(
                    job_id=input.job_id,
                    field_key=input.field_key,
                    question_text=input.question_text,
                    question_hash=input.question_hash,
                    expires_at=expires_at,
                )
                webhook_emitted = True
            except Exception as exc:
                logger.warning(
                    "human_input_await: failed to emit webhook job_id=%s field_key=%s: %s",
                    input.job_id,
                    input.field_key,
                    exc,
                )
                # Non-fatal — continue heartbeat loop; webhook will be retried next iteration

        # TODO: ping BrowserSidecar via Unix socket when sidecar IPC is wired (ADR-0019)
        # For now, assume sidecar is alive (skeleton implementation)
        sidecar_alive = True

        if not sidecar_alive:
            raise ApplicationError(
                "BrowserSidecar unreachable during human-input wait",
                non_retryable=True,
                type="sidecar_unreachable",
            )

        # Report heartbeat to Temporal
        activity.heartbeat(sidecar_alive=True, ping_count=heartbeat_count)
        heartbeat_count += 1

        logger.debug(
            "human_input_await: heartbeat job_id=%s field_key=%s count=%d",
            input.job_id,
            input.field_key,
            heartbeat_count,
        )

        try:
            await asyncio.sleep(input.heartbeat_interval_s)
        except asyncio.CancelledError:
            # Activity was cancelled — signal arrived or workflow was cancelled
            logger.info(
                "human_input_await: cancelled (signal received) job_id=%s field_key=%s heartbeats=%d",
                input.job_id,
                input.field_key,
                heartbeat_count,
            )
            return HumanInputAwaitResult(sidecar_alive=True, heartbeat_count=heartbeat_count)


def _emit_human_input_required_webhook(
    job_id: str,
    field_key: str,
    question_text: str,
    question_hash: str,
    expires_at: str,
) -> None:
    """Enqueue a job.human_input_required webhook in the outbox.

    Uses WebhookDispatcher from open-banca-webhooks if available.
    Falls back to no-op with a warning in test / environments without the package.
    """
    import uuid
    from datetime import UTC, datetime

    try:
        from open_banca_webhooks.dispatcher import WebhookDispatcher  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "human_input_await: open-banca-webhooks not available — webhook not emitted "
            "job_id=%s field_key=%s",
            job_id,
            field_key,
        )
        return

    target_url = os.environ.get("OPEN_BANCA_WEBHOOK_TARGET_URL", "")
    secret = os.environ.get("OPEN_BANCA_WEBHOOK_SECRET", "")
    db_path = os.environ.get("OPEN_BANCA_WEBHOOK_DB_PATH", "./webhook_outbox.db")

    if not target_url or not secret:
        logger.warning(
            "human_input_await: OPEN_BANCA_WEBHOOK_TARGET_URL or OPEN_BANCA_WEBHOOK_SECRET "
            "not configured — webhook not emitted job_id=%s",
            job_id,
        )
        return

    from open_banca_domain.entities.webhook_event import WebhookEvent, WebhookEventType

    dispatcher = WebhookDispatcher(target_url=target_url, secret=secret, db_path=db_path)
    event = WebhookEvent(
        id=str(uuid.uuid4()),
        event_type=WebhookEventType.JOB_HUMAN_INPUT_REQUIRED,
        payload={
            "job_id": job_id,
            "field_key": field_key,
            "question_text": question_text,  # Pre-filtered by PII redact (ADR-0020) at runner level
            "question_hash_prefix": question_hash[:8],
            "cached_attempted": True,
            "expires_at": expires_at,
        },
        signature="",
        dispatched_at=datetime.now(UTC),
        job_id=job_id,
    )
    dispatcher.publish(event)
