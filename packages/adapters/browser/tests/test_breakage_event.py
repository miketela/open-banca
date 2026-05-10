"""Tests for breakage.py — BreakageEvent construction from step failures."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from open_banca_browser.breakage import build_breakage_event
from open_banca_browser.errors import (
    AssertionFailed,
    DownloadFailed,
    HTTPError,
    SchemaMismatch,
    SelectorNotFound,
    StepTimeout,
)


@pytest.fixture()
def mock_page() -> MagicMock:
    page = MagicMock()
    page.screenshot.return_value = b"\x89PNG\r\n\x1a\nfake"
    page.evaluate.return_value = "<div>mock dom</div>"
    page.locator.return_value.evaluate.return_value = "<div>mock dom</div>"
    return page


def test_build_breakage_event_step_timeout(mock_page: MagicMock) -> None:
    error = StepTimeout("timeout on #submit")
    event = build_breakage_event(
        job_id="job-1",
        step_index=2,
        step_type="click",
        error=error,
        screenshot_png=b"\x89PNG\r\n\x1a\nfake",
        page=mock_page,
        selector="#submit",
    )
    assert event.job_id == "job-1"
    assert event.step_index == 2
    assert event.step_type == "click"
    assert event.error_class == "step_timeout"
    assert event.screenshot_ref.startswith("sha256:")
    assert event.http_status is None


def test_build_breakage_event_http_error(mock_page: MagicMock) -> None:
    error = HTTPError("HTTP 404", http_status=404)
    event = build_breakage_event(
        job_id="job-2",
        step_index=0,
        step_type="navigate",
        error=error,
        screenshot_png=b"fake_png",
        page=mock_page,
    )
    assert event.error_class == "http_error"
    assert event.http_status == 404


def test_build_breakage_event_schema_mismatch(mock_page: MagicMock) -> None:
    error = SchemaMismatch("missing column 'Fecha'")
    event = build_breakage_event(
        job_id="job-3",
        step_index=5,
        step_type="extract_table",
        error=error,
        screenshot_png=b"fake",
        page=mock_page,
    )
    assert event.error_class == "schema_drift"


def test_build_breakage_event_selector_not_found(mock_page: MagicMock) -> None:
    error = SelectorNotFound("not found: #login")
    event = build_breakage_event(
        job_id="job-4",
        step_index=1,
        step_type="fill",
        error=error,
        screenshot_png=b"fake",
        page=mock_page,
    )
    assert event.error_class == "selector_missing"


def test_build_breakage_event_assertion_failed(mock_page: MagicMock) -> None:
    error = AssertionFailed("text mismatch")
    event = build_breakage_event(
        job_id="job-5",
        step_index=3,
        step_type="assert_text",
        error=error,
        screenshot_png=b"fake",
        page=mock_page,
    )
    assert event.error_class == "assertion_failed"


def test_build_breakage_event_download_failed(mock_page: MagicMock) -> None:
    error = DownloadFailed("download aborted")
    event = build_breakage_event(
        job_id="job-6",
        step_index=4,
        step_type="download_file",
        error=error,
        screenshot_png=b"fake",
        page=mock_page,
    )
    assert event.error_class == "download_aborted"


def test_breakage_event_screenshot_ref_deterministic(mock_page: MagicMock) -> None:
    """Same bytes → same sha256 ref."""
    png = b"\x89PNG\r\n\x1a\nfake_content"
    error = StepTimeout("t")
    e1 = build_breakage_event(
        job_id="j", step_index=0, step_type="click",
        error=error, screenshot_png=png, page=mock_page,
    )
    e2 = build_breakage_event(
        job_id="j", step_index=0, step_type="click",
        error=error, screenshot_png=png, page=mock_page,
    )
    assert e1.screenshot_ref == e2.screenshot_ref


def test_breakage_event_dom_excerpt_present(mock_page: MagicMock) -> None:
    mock_page.evaluate.return_value = "<body><input value='secret'></body>"
    error = StepTimeout("t")
    event = build_breakage_event(
        job_id="j", step_index=0, step_type="navigate",
        error=error, screenshot_png=b"fake", page=mock_page,
    )
    # DOM excerpt should NOT contain the raw value= content
    assert "secret" not in event.dom_excerpt
    assert "[REDACTED]" in event.dom_excerpt
