"""record_har — helper for recording Playwright network traffic to a HAR file.

This module is intended for **development and debug use only**.
The produced HAR will contain raw credentials and must be sanitized with
``HARSanitizer`` before it can be committed to the repository.

Usage::

    from pathlib import Path
    from playwright.sync_api import sync_playwright
    from open_banca_browser.har.record import record_har

    def my_scrape(page):
        page.goto("https://bank.example.com")
        # ... interact ...

    with sync_playwright() as p:
        browser = p.chromium.launch()
        record_har(
            browser=browser,
            output_path=Path("raw/banco_general_login.har"),
            run_callable=my_scrape,
        )
        browser.close()
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# HAR 1.2 schema version constant
HAR_VERSION = "1.2"


def record_har(
    browser: Any,
    output_path: Path,
    run_callable: Callable[[Any], None],
    *,
    url_filter: str | None = None,
    record_har_content: str = "embed",
    headless_warning: bool = True,
) -> Path:
    """Record all network traffic during *run_callable* to a HAR file.

    Opens a new Playwright BrowserContext configured for HAR recording,
    creates a Page, calls ``run_callable(page)``, closes the context
    (which flushes the HAR to disk), and returns *output_path*.

    Args:
        browser: A Playwright ``Browser`` object (sync API).
        output_path: Destination path for the raw HAR file.  Parent
            directories are created automatically.
        run_callable: A callable that receives a ``Page`` and performs
            the interactions to be recorded.
        url_filter: Optional URL glob pattern (e.g. ``"**/*.banco.com/**"``).
            Only matching requests are recorded.  Defaults to all URLs.
        record_har_content: HAR content policy.  ``"embed"`` inlines
            response bodies; ``"attach"`` stores them separately;
            ``"omit"`` skips response bodies entirely.  Defaults to
            ``"embed"`` so replays can serve full responses.
        headless_warning: If True, log a warning reminding the caller to
            sanitize the output before committing.

    Returns:
        The *output_path* after successful write.

    Raises:
        RuntimeError: If the browser is closed or the context creation fails.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if headless_warning:
        logger.warning(
            "record_har: output '%s' may contain raw credentials — "
            "run HARSanitizer before committing.",
            output_path,
        )

    context_kwargs: dict[str, Any] = {
        "record_har_path": str(output_path),
        "record_har_content": record_har_content,
    }
    if url_filter is not None:
        context_kwargs["record_har_url_filter"] = url_filter

    context = browser.new_context(**context_kwargs)
    try:
        page = context.new_page()
        run_callable(page)
    finally:
        # Closing the context flushes the HAR file.
        context.close()

    logger.info("HAR recorded to: %s", output_path)
    return output_path


def load_har(path: Path) -> dict[str, Any]:
    """Load a HAR file from disk and return the parsed dict.

    Validates that the file contains a ``log`` key with HAR 1.2
    ``version`` field.

    Raises:
        ValueError: If the file does not look like a valid HAR 1.2 document.
    """
    with path.open(encoding="utf-8") as fh:
        har: dict[str, Any] = json.load(fh)

    log = har.get("log")
    if not isinstance(log, dict):
        raise ValueError(f"Invalid HAR: missing 'log' object in {path}")

    version: str = log.get("version", "")
    if not version.startswith(HAR_VERSION):
        raise ValueError(
            f"Unexpected HAR version {version!r} in {path}; expected {HAR_VERSION}.x"
        )

    return har
