"""WebhookEvent entity — 7 canonical outbound events."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class WebhookEventType(StrEnum):
    """The 7 webhook event types per ADR-0011 / REQ-010."""

    JOB_CREATED = "job.created"
    JOB_OTP_REQUIRED = "job.otp_required"
    JOB_PROGRESS = "job.progress"
    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"
    JOB_REMAP_PROPOSED = "job.remap_proposed"
    JOB_HUMAN_REQUIRED = "job.human_required"


class WebhookEvent(BaseModel):
    """Immutable outbound webhook event with HMAC signature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    event_type: WebhookEventType
    payload: dict[str, Any]
    signature: str
    dispatched_at: datetime
    retries: int = 0
    job_id: str | None = None
