"""BreakageEvent entity — captures scrape step failure details."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BreakageEvent(BaseModel):
    """Immutable record of a scrape step failure for remap triggering.

    Required by the Judge agent (ADR-0013) to decide between retry,
    remap-patch, remap-full, and human escalation.

    Fields
    ------
    job_id          : Unique job identifier.
    step_index      : Zero-based position of the failing step.
    step_type       : Action string (e.g. 'click', 'navigate').
    error_class     : Cause token from ScraperError.error_class.
    screenshot_ref  : Content-addressed ref ('sha256:<hex>').
    dom_excerpt     : Up to 50 KB of redacted HTML from the failing frame.
    occurred_at     : UTC timestamp of the failure.
    http_status     : HTTP status code if error_class is 'http_error'.
    selector_attempted : CSS/XPath selector that was tried (if any).
    expected        : Expected value or state described in the step spec.
    observed        : Actual value or state observed at failure time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    step_index: int
    step_type: str
    error_class: str
    screenshot_ref: str
    dom_excerpt: str
    occurred_at: datetime
    http_status: int | None = None
    # Evidence fields for Judge agent — optional to preserve backward compat
    # with T9 callers; step executors populate when available.
    selector_attempted: str | None = None
    expected: str | None = None
    observed: str | None = None
