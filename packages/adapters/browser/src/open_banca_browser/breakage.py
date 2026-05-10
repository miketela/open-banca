"""BreakageEvent factory — builds domain entity from runner error context."""
from __future__ import annotations

from datetime import UTC, datetime

from open_banca_browser.errors import ScraperError
from open_banca_browser.screenshot import screenshot_ref
from open_banca_domain.entities.breakage_event import BreakageEvent


def build_breakage_event(
    *,
    job_id: str,
    step_index: int,
    step_type: str,
    error: ScraperError | Exception,
    screenshot_png: bytes,
    page: object,
    selector: str | None = None,
) -> BreakageEvent:
    """Construct a BreakageEvent from a step failure.

    Args:
        job_id: Unique identifier for the scrape job.
        step_index: Zero-based index of the failing step.
        step_type: The action string of the failing step (e.g. 'click').
        error: The exception that caused the breakage.
        screenshot_png: PNG bytes captured after failure.
        page: Playwright Page object (used to extract DOM excerpt).
        selector: Optional selector to narrow DOM excerpt.

    Returns:
        An immutable BreakageEvent domain entity.
    """
    from open_banca_browser.screenshot import dom_excerpt  # avoid circular at module level

    error_class = getattr(error, "error_class", "unknown")
    http_status: int | None = getattr(error, "http_status", None)
    ref = screenshot_ref(screenshot_png)
    excerpt = dom_excerpt(page, selector)

    return BreakageEvent(
        job_id=job_id,
        step_index=step_index,
        step_type=step_type,
        error_class=error_class,
        screenshot_ref=ref,
        dom_excerpt=excerpt,
        occurred_at=datetime.now(tz=UTC),
        http_status=http_status,
    )
