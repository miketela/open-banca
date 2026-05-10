"""Tests for dom_excerpt 50 KB truncation (task-10).

TDD coverage:
- test_dom_truncation_100kb: A 100 KB dom string is truncated to ≤50 KB with [TRUNCATED] marker.
- test_dom_no_truncation_under_50kb: A 10 KB dom string is returned as-is (no marker).
- test_dom_truncation_exact_50kb: Exactly 50 KB is not truncated.
- test_dom_truncation_marker_position: [TRUNCATED] marker appears at the end.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from open_banca_browser.screenshot import _DOM_SNAPSHOT_MAX_BYTES, dom_excerpt


@pytest.fixture()
def mock_page_with_content(request):  # type: ignore[no-untyped-def]
    """Build a mock Playwright page whose body.innerHTML returns a string of given size."""
    content: str = request.param
    page = MagicMock()
    page.evaluate.return_value = content
    return page


def _make_safe_html(size_bytes: int) -> str:
    """Generate safe HTML of approximately `size_bytes` bytes (no value= attributes)."""
    chunk = "<span>x</span>"
    repeats = (size_bytes // len(chunk)) + 1
    return (chunk * repeats)[:size_bytes]


def test_dom_truncation_100kb() -> None:
    """100 KB DOM is truncated to ≤50 KB with [TRUNCATED] marker appended."""
    raw = _make_safe_html(100 * 1024)
    page = MagicMock()
    page.evaluate.return_value = raw

    result = dom_excerpt(page)

    assert "[TRUNCATED]" in result
    # Strip the trailing marker (including its leading newline) to measure the
    # content portion — it must fit within the 50 KB hard cap.
    content_part = result.replace("\n[TRUNCATED]", "")
    assert len(content_part.encode("utf-8")) <= _DOM_SNAPSHOT_MAX_BYTES


def test_dom_no_truncation_under_50kb() -> None:
    """10 KB DOM is returned as-is without a [TRUNCATED] marker."""
    raw = _make_safe_html(10 * 1024)
    page = MagicMock()
    page.evaluate.return_value = raw

    result = dom_excerpt(page)

    assert "[TRUNCATED]" not in result
    assert len(result.encode("utf-8")) <= _DOM_SNAPSHOT_MAX_BYTES


def test_dom_truncation_exact_50kb() -> None:
    """A DOM of exactly 50 KB is NOT truncated (boundary is inclusive)."""
    raw = _make_safe_html(_DOM_SNAPSHOT_MAX_BYTES)
    page = MagicMock()
    page.evaluate.return_value = raw

    result = dom_excerpt(page)

    assert "[TRUNCATED]" not in result
    assert len(result.encode("utf-8")) <= _DOM_SNAPSHOT_MAX_BYTES


def test_dom_truncation_marker_at_end() -> None:
    """The [TRUNCATED] marker is appended at the very end of the string."""
    raw = _make_safe_html(100 * 1024)
    page = MagicMock()
    page.evaluate.return_value = raw

    result = dom_excerpt(page)

    assert result.endswith("[TRUNCATED]")


def test_dom_truncation_with_value_redaction() -> None:
    """Redaction and truncation cooperate: value= is redacted AND 100 KB is truncated."""
    # value= attributes get replaced with [REDACTED], changing length slightly.
    chunk = '<input value="secret123">'
    raw = chunk * ((100 * 1024 // len(chunk)) + 1)
    page = MagicMock()
    page.evaluate.return_value = raw

    result = dom_excerpt(page)

    assert "secret123" not in result
    assert "[TRUNCATED]" in result
    content_part = result.replace("\n[TRUNCATED]", "")
    assert len(content_part.encode("utf-8")) <= _DOM_SNAPSHOT_MAX_BYTES
