"""Request/response schemas for POST /scrape."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ScrapeRequest(BaseModel):
    """Payload for POST /scrape."""

    model_config = ConfigDict(extra="forbid")

    bank_id: str = Field(..., description="Bank identifier (e.g. 'banco_general').")
    credentials: str = Field(
        ...,
        description="Passthrough encrypted credential blob (opaque to API layer).",
    )
    accounts: list[str] = Field(
        default_factory=list,
        description="Account IDs to scrape. Empty list means all accounts.",
    )
    full: bool = Field(
        default=False,
        description="True for full historical scrape; False for incremental.",
    )
    since: datetime | None = Field(
        default=None,
        description="Cursor timestamp for incremental mode (ISO-8601 UTC).",
    )
    webhook_url: str | None = Field(
        default=None,
        description="URL to POST webhook events to.",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Free-form metadata attached to the job.",
    )


class ScrapeResponse(BaseModel):
    """202 Accepted response for POST /scrape."""

    model_config = ConfigDict(frozen=True)

    job_id: str
    status: str = "pending"
    created_at: datetime
