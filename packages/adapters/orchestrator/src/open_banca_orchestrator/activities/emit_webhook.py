"""EmitWebhookActivity — deliver a signed webhook event to the configured endpoint.

Retry policy (orchestrator.md §Inventario):
  - Exponential backoff, 5 attempts, max retry interval 1 h.
  - start-to-close timeout: 10 s per attempt.
  - NO heartbeat (short-running HTTP call).

Idempotency key: event_id (UUID v7) — webhook receiver should deduplicate.

HMAC-SHA256 signature on the payload per ADR security requirements.

IMPLEMENTATION STATUS: skeleton — raises NotImplementedError.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field
from temporalio import activity


class WebhookEventType(StrEnum):
    """Webhook event types emitted by the orchestrator."""

    job_completed = "job.completed"
    job_failed = "job.failed"
    job_otp_required = "job.otp_required"
    job_cancelled = "job.cancelled"
    remap_proposed = "job.remap_proposed"
    remap_completed = "job.remap_completed"


class WebhookEvent(BaseModel):
    """Webhook event payload."""

    event_id: str = Field(description="UUID v7 event identifier for idempotency")
    event_type: WebhookEventType
    job_id: str
    timestamp: str = Field(description="ISO 8601 UTC timestamp")
    payload: dict[str, object] = Field(
        default_factory=dict,
        description="Event-type-specific payload fields",
    )


class EmitWebhookInput(BaseModel):
    """Input for EmitWebhookActivity."""

    event: WebhookEvent = Field(description="Webhook event to deliver")


class EmitWebhookResult(BaseModel):
    """Result from EmitWebhookActivity."""

    enqueued: bool = Field(description="True if webhook was accepted by the endpoint")
    http_status: int | None = Field(
        default=None,
        description="HTTP response status from the endpoint",
    )
    attempt_number: int = Field(
        default=1,
        description="Which retry attempt succeeded (1 = first try)",
    )


class EmitWebhookActivity:
    """EmitWebhookActivity class-based wrapper."""


@activity.defn(name="EmitWebhookActivity")
async def emit_webhook(input: EmitWebhookInput) -> EmitWebhookResult:  # noqa: A002
    """Deliver a signed HMAC-SHA256 webhook event to the configured endpoint.

    Retry is handled by Temporal (5 attempts, exp backoff, max 1 h interval).
    Activity itself performs a single HTTP POST attempt.

    TODO: implement HMAC-SHA256 signing with webhook secret from secrets store.
    TODO: use httpx AsyncClient for the HTTP POST.
    TODO: on 4xx non-retryable: raise ApplicationError(non_retryable=True).
    TODO: on 5xx retryable: raise ApplicationError(non_retryable=False) to let Temporal retry.
    """
    raise NotImplementedError(
        "EmitWebhookActivity not implemented — wired in task implementing webhook delivery"
    )
