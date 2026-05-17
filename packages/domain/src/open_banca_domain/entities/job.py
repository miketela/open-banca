"""Job entity — represents one scrape execution lifecycle."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class JobStatus(StrEnum):
    """Exhaustive set of job states per orchestrator.md state diagram."""

    PENDING = "pending"
    RUNNING = "running"
    OTP_REQUIRED = "otp_required"
    HUMAN_INPUT_REQUIRED = "human_input_required"
    RESUMED = "resumed"
    ESCALATED = "escalated"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobMode(StrEnum):
    """Scrape mode — full historical or incremental since cursor."""

    FULL = "full"
    INCREMENTAL = "incremental"


class Job(BaseModel):
    """Immutable aggregate root for a single scrape execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    status: JobStatus
    bank: str
    credential_ref: str
    mode: JobMode
    created_at: datetime
    updated_at: datetime
    since_cursor: str | None = None
    error: str | None = None
