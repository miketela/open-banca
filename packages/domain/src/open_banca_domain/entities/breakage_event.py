"""BreakageEvent entity — captures scrape step failure details."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BreakageEvent(BaseModel):
    """Immutable record of a scrape step failure for remap triggering."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    step_index: int
    step_type: str
    error_class: str
    screenshot_ref: str
    dom_excerpt: str
    occurred_at: datetime
    http_status: int | None = None
