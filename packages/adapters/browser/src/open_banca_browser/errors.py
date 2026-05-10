"""Custom exceptions for the Playwright scraper runner."""
from __future__ import annotations


class ScraperError(Exception):
    """Base class for all scraper errors."""

    def __init__(self, message: str, step_index: int | None = None) -> None:
        super().__init__(message)
        self.step_index = step_index


class StepTimeout(ScraperError):
    """Raised when a step exceeds its allowed timeout."""

    error_class = "step_timeout"


class SelectorNotFound(ScraperError):
    """Raised when a CSS/XPath selector cannot be found in the DOM."""

    error_class = "selector_missing"


class SchemaMismatch(ScraperError):
    """Raised when extracted data does not match expected schema (schema drift)."""

    error_class = "schema_drift"


class HTTPError(ScraperError):
    """Raised when a navigation results in a 4xx/5xx HTTP response."""

    error_class = "http_error"

    def __init__(self, message: str, http_status: int, step_index: int | None = None) -> None:
        super().__init__(message, step_index)
        self.http_status = http_status


class DownloadFailed(ScraperError):
    """Raised when a file download is aborted or has the wrong MIME/extension."""

    error_class = "download_aborted"


class AssertionFailed(ScraperError):
    """Raised when assert_text does not match the expected value."""

    error_class = "assertion_failed"


class WidgetUnsupported(ScraperError):
    """Raised when a date picker widget type is not supported."""

    error_class = "widget_unsupported"


class MimeMismatch(ScraperError):
    """Raised when a downloaded file has an unexpected MIME type or extension."""

    error_class = "mime_mismatch"


class ValueNotResolved(ScraperError):
    """Raised when a value_ref cannot be resolved from the secret vault."""

    error_class = "value_not_resolved"
