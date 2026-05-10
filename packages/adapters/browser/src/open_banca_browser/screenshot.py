"""Screenshot capture with PII redaction (Layer 2 protection).

ADR-0020 is not yet written; this implements a conservative minimal strategy:
- Before capturing, blank all input[type=password] and elements with data-sensitive
  attribute via JavaScript injection.
- DOM excerpts strip attribute values for sensitive inputs.
"""
from __future__ import annotations

import hashlib
import logging
import re

logger = logging.getLogger(__name__)

# JS that blanks sensitive inputs before screenshot
_REDACT_JS = """
() => {
    const selectors = [
        'input[type="password"]',
        'input[data-sensitive="true"]',
        '[data-sensitive="true"]',
    ];
    selectors.forEach(sel => {
        document.querySelectorAll(sel).forEach(el => {
            if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                el.value = '';
            }
            el.setAttribute('data-redacted', '1');
        });
    });
}
"""

# Regex to strip value= attributes that may contain secrets from DOM excerpts.
# Handles both double-quoted and single-quoted attribute values.
_VALUE_ATTR_RE = re.compile(
    r'(value\s*=\s*)(["\'])(?:(?!\2).)*(\2)',
    re.IGNORECASE,
)


def capture_screenshot(page: object) -> bytes:
    """Capture a screenshot after redacting sensitive DOM elements.

    Args:
        page: A Playwright Page object.

    Returns:
        PNG bytes of the screenshot.
    """
    try:
        page.evaluate(_REDACT_JS)  # type: ignore[attr-defined]
    except Exception:
        logger.warning("PII redact JS injection failed; proceeding with raw screenshot")
    return page.screenshot(type="png")  # type: ignore[attr-defined]


def dom_excerpt(page: object, selector: str | None = None) -> str:
    """Return a short DOM snippet for debugging, with value= attributes stripped.

    Args:
        page: A Playwright Page object.
        selector: Optional CSS selector to narrow the excerpt to a subtree.

    Returns:
        Up to 2000 chars of HTML with value= attributes redacted.
    """
    try:
        if selector:
            locator = page.locator(selector)  # type: ignore[attr-defined]
            raw = locator.evaluate("el => el.outerHTML")
        else:
            raw = page.evaluate("() => document.body.innerHTML")  # type: ignore[attr-defined]
    except Exception as exc:
        return f"<dom_excerpt_error: {exc}>"

    redacted = _VALUE_ATTR_RE.sub(r'\1\2[REDACTED]\3', raw)
    return redacted[:2000]


def screenshot_ref(png_bytes: bytes) -> str:
    """Generate a stable content-addressed reference for a screenshot.

    Args:
        png_bytes: Raw PNG bytes.

    Returns:
        A sha256 hex digest prefixed with 'sha256:'.
    """
    digest = hashlib.sha256(png_bytes).hexdigest()
    return f"sha256:{digest}"
