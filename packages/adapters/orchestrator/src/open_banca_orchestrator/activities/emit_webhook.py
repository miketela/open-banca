"""EmitWebhookActivity — enqueue a signed webhook event via WebhookDispatcher.

Retry policy (orchestrator.md §Inventario):
  - The activity itself only enqueues in the outbox (no network I/O).
  - Actual HTTP delivery + retry is handled by the webhook worker process.
  - Temporal retry policy covers transient outbox write failures.

Idempotency key: event_id (UUID) — webhook receiver should deduplicate.

HMAC-SHA256 signature on the payload per ADR-0011:
  X-OpenBanca-Signature: t=<ts>,v1=<hex_hmac>,n=<nonce>
  Window: 60 seconds. Nonce: required.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from temporalio import activity
from temporalio.exceptions import ApplicationError

from open_banca_domain.entities.webhook_event import WebhookEvent, WebhookEventType


class EmitWebhookInput(BaseModel):
    """Input for EmitWebhookActivity."""

    event_id: str = Field(description="UUID event identifier for idempotency")
    event_type: str = Field(description="One of the 7 webhook event types")
    job_id: str = Field(description="Associated scrape job ID")
    payload: dict[str, object] = Field(
        default_factory=dict,
        description="Event-type-specific payload fields",
    )


class EmitWebhookResult(BaseModel):
    """Result from EmitWebhookActivity."""

    enqueued: bool = Field(description="True if webhook was enqueued in outbox")
    event_id: str = Field(description="The enqueued event ID")


class EmitWebhookActivity:
    """EmitWebhookActivity class-based wrapper (unused, kept for compatibility)."""


@activity.defn(name="EmitWebhookActivity")
async def emit_webhook(input: EmitWebhookInput) -> EmitWebhookResult:  # noqa: A002
    """Enqueue a signed HMAC-SHA256 webhook event in the outbox.

    Delegates to WebhookDispatcher.publish() which writes to the SQLite outbox.
    The webhook worker process performs the actual HTTP delivery with retry.

    Raises ApplicationError(non_retryable=True) for configuration errors.
    Raises ApplicationError(non_retryable=False) for transient write failures.
    """
    try:
        from open_banca_webhooks.dispatcher import WebhookDispatcher
    except ImportError as exc:
        raise ApplicationError(
            "WebhookDispatcher not available — open-banca-webhooks package missing",
            non_retryable=True,
        ) from exc

    target_url = os.environ.get("OPEN_BANCA_WEBHOOK_TARGET_URL", "")
    secret = os.environ.get("OPEN_BANCA_WEBHOOK_SECRET", "")
    db_path = os.environ.get("OPEN_BANCA_WEBHOOK_DB_PATH", "./webhook_outbox.db")

    if not target_url or not secret:
        activity.logger.warning(
            "webhook skipped (dev): OPEN_BANCA_WEBHOOK_TARGET_URL or "
            "OPEN_BANCA_WEBHOOK_SECRET not set job_id=%s event_type=%s",
            input.job_id,
            input.event_type,
        )
        return EmitWebhookResult(enqueued=False, event_id=input.event_id)

    try:
        event_type = WebhookEventType(input.event_type)
    except ValueError as exc:
        raise ApplicationError(
            f"Invalid event_type: {input.event_type}",
            non_retryable=True,
        ) from exc

    dispatcher = WebhookDispatcher(
        target_url=target_url,
        secret=secret,
        db_path=db_path,
    )

    event = WebhookEvent(
        id=input.event_id,
        event_type=event_type,
        payload=input.payload,
        signature="",
        dispatched_at=datetime.now(UTC),
        job_id=input.job_id,
    )

    try:
        dispatcher.publish(event)
    except Exception as exc:
        raise ApplicationError(
            f"Failed to enqueue webhook event: {exc}",
            non_retryable=False,
        ) from exc

    return EmitWebhookResult(enqueued=True, event_id=input.event_id)
