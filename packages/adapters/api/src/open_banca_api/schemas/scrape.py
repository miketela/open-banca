"""Request/response schemas for POST /scrape."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScrapeRequest(BaseModel):
    """Payload for POST /scrape.

    Mode resolution (priority order):
      1. ``mode`` field if explicitly provided ("full" | "incremental").
      2. Legacy ``full: True`` maps to mode="full".
      3. Default: mode="incremental".

    If mode is "incremental" and ``since`` is None, the API layer queries
    ``DedupEngine.effective_since(account_id)`` per account at request time.
    If no cursor exists (first run) the mode falls back to "full" automatically.
    """

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
    mode: Literal["full", "incremental"] = Field(
        default="incremental",
        description=(
            "Scrape mode: 'full' fetches full history (6-month default); "
            "'incremental' resumes from last cursor (cursor - 3d buffer)."
        ),
    )
    full: bool = Field(
        default=False,
        description="Deprecated. Use mode='full' instead. True overrides mode to 'full'.",
    )
    since: datetime | None = Field(
        default=None,
        description=(
            "Optional cursor override for incremental mode (ISO-8601 UTC). "
            "When None and mode='incremental', the cursor is derived from "
            "DedupEngine.effective_since(account_id) at request time."
        ),
    )
    webhook_url: str | None = Field(
        default=None,
        description="URL to POST webhook events to.",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Free-form metadata attached to the job.",
    )

    @model_validator(mode="after")
    def _resolve_legacy_full(self) -> ScrapeRequest:
        """Promote legacy ``full=True`` to ``mode='full'``."""
        if self.full and self.mode == "incremental":
            # Use object.__setattr__ to bypass Pydantic frozen check (model is not frozen)
            object.__setattr__(self, "mode", "full")
        return self


class ScrapeResponse(BaseModel):
    """202 Accepted response for POST /scrape."""

    model_config = ConfigDict(frozen=True)

    job_id: str
    status: str = "pending"
    created_at: datetime


class JobCursorResponse(BaseModel):
    """Response for GET /jobs/{id}/cursor — current per-account cursors."""

    model_config = ConfigDict(frozen=True)

    job_id: str
    cursors: dict[str, str] = Field(
        description="Map of account_id → ISO-8601 effective_since datetime (cursor - 3d)."
    )
