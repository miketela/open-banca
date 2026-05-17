"""Unit tests for all 9 step executors using mock Page objects."""
from __future__ import annotations

import pathlib
from unittest.mock import MagicMock

import pytest

from open_banca_browser.errors import (
    AssertionFailed,
    DownloadFailed,
    HTTPError,
    MimeMismatch,
    SchemaMismatch,
    SelectorNotFound,
    StepTimeout,
    WidgetUnsupported,
)
from open_banca_browser.step_executors.assert_text import execute_assert_text
from open_banca_browser.step_executors.click import execute_click
from open_banca_browser.step_executors.download_file import execute_download_file
from open_banca_browser.step_executors.extract_table import execute_extract_table
from open_banca_browser.step_executors.navigate import execute_navigate
from open_banca_browser.step_executors.select_date_range import execute_select_date_range
from open_banca_browser.step_executors.wait_for_download import execute_wait_for_download
from open_banca_browser.step_executors.wait_for_selector import execute_wait_for_selector
from open_banca_domain.entities.bank_map import StepSpec


def _step(**extra: object) -> StepSpec:
    return StepSpec(step_id="test-step", action="test", **extra)  # type: ignore[arg-type]


# ─────────────────────────────── navigate ────────────────────────────────────

class TestNavigate:
    def test_success(self) -> None:
        page = MagicMock()
        response = MagicMock()
        response.status = 200
        page.goto.return_value = response
        step = _step(url="https://example.com", wait_until="load")
        execute_navigate(page, step)
        page.goto.assert_called_once_with(
            "https://example.com", wait_until="load", timeout=30_000
        )

    def test_http_error_raises(self) -> None:
        page = MagicMock()
        response = MagicMock()
        response.status = 404
        page.goto.return_value = response
        step = _step(url="https://example.com/missing")
        with pytest.raises(HTTPError) as exc_info:
            execute_navigate(page, step)
        assert exc_info.value.http_status == 404

    def test_timeout_raises(self) -> None:
        page = MagicMock()
        page.goto.side_effect = Exception("Timeout exceeded")
        step = _step(url="https://slow.example.com")
        with pytest.raises(StepTimeout):
            execute_navigate(page, step)

    def test_none_response_is_ok(self) -> None:
        """goto returning None (e.g. same-page navigation) should not raise."""
        page = MagicMock()
        page.goto.return_value = None
        step = _step(url="https://example.com")
        execute_navigate(page, step)  # no exception


# ─────────────────────────────── click ───────────────────────────────────────

class TestClick:
    def _make_page(self, count: int = 1) -> MagicMock:
        page = MagicMock()
        locator = MagicMock()
        locator.count.return_value = count
        locator.nth.return_value = locator
        page.locator.return_value = locator
        return page

    def test_success(self) -> None:
        page = self._make_page()
        step = _step(selector="#submit")
        execute_click(page, step)
        page.locator.return_value.click.assert_called_once_with(
            timeout=15_000, force=False
        )

    def test_selector_not_found(self) -> None:
        page = self._make_page(count=0)
        step = _step(selector="#ghost")
        with pytest.raises(SelectorNotFound):
            execute_click(page, step)

    def test_click_nth(self) -> None:
        page = self._make_page(count=3)
        step = _step(selector=".item", nth=1)
        execute_click(page, step)
        page.locator.return_value.nth.assert_called_with(1)

    def test_click_timeout_raises(self) -> None:
        page = self._make_page()
        page.locator.return_value.click.side_effect = Exception("Timeout exceeded")
        step = _step(selector="#submit")
        with pytest.raises(StepTimeout):
            execute_click(page, step)


# ─────────────────────────────── wait_for_selector ───────────────────────────

class TestWaitForSelector:
    def test_success(self) -> None:
        page = MagicMock()
        step = _step(selector="#modal", state="visible", timeout_ms=5000)
        execute_wait_for_selector(page, step)
        page.wait_for_selector.assert_called_once_with(
            "#modal", state="visible", timeout=5000
        )

    def test_timeout_raises(self) -> None:
        page = MagicMock()
        page.wait_for_selector.side_effect = Exception("Timeout exceeded")
        step = _step(selector="#slow", state="visible", timeout_ms=100)
        with pytest.raises(StepTimeout):
            execute_wait_for_selector(page, step)

    def test_not_found_raises(self) -> None:
        page = MagicMock()
        page.wait_for_selector.side_effect = Exception("Element not found in DOM")
        step = _step(selector="#ghost")
        with pytest.raises(SelectorNotFound):
            execute_wait_for_selector(page, step)

    def test_invalid_state_defaults_to_visible(self) -> None:
        page = MagicMock()
        step = _step(selector="#el", state="invalid_state")
        execute_wait_for_selector(page, step)
        args, kwargs = page.wait_for_selector.call_args
        assert kwargs.get("state") == "visible" or args[1] == "visible"  # type: ignore[misc]


# ─────────────────────────────── assert_text ─────────────────────────────────

class TestAssertText:
    def _make_page(self, text: str = "Hello World", count: int = 1) -> MagicMock:
        page = MagicMock()
        locator = MagicMock()
        locator.count.return_value = count
        locator.inner_text.return_value = text
        page.locator.return_value = locator
        return page

    def test_contains_pass(self) -> None:
        page = self._make_page("Welcome to the bank")
        step = _step(selector="#greeting", expected="Welcome", mode="contains")
        execute_assert_text(page, step)  # no exception

    def test_exact_pass(self) -> None:
        page = self._make_page("Exact Match")
        step = _step(selector="#el", expected="Exact Match", mode="exact")
        execute_assert_text(page, step)

    def test_regex_pass(self) -> None:
        page = self._make_page("Balance: $1,234.56")
        step = _step(selector="#balance", expected=r"\$[\d,]+\.\d{2}", mode="regex")
        execute_assert_text(page, step)

    def test_contains_fail_raises(self) -> None:
        page = self._make_page("Something else")
        step = _step(selector="#el", expected="Welcome", mode="contains")
        with pytest.raises(AssertionFailed):
            execute_assert_text(page, step)

    def test_exact_fail_raises(self) -> None:
        page = self._make_page("Close but not exact")
        step = _step(selector="#el", expected="Exact", mode="exact")
        with pytest.raises(AssertionFailed):
            execute_assert_text(page, step)

    def test_selector_not_found_raises(self) -> None:
        page = self._make_page(count=0)
        step = _step(selector="#ghost", expected="text")
        with pytest.raises(SelectorNotFound):
            execute_assert_text(page, step)


# ─────────────────────────────── extract_table ───────────────────────────────

class TestExtractTable:
    def _make_table_page(
        self,
        headers: list[str],
        rows: list[list[str]],
    ) -> MagicMock:
        page = MagicMock()
        table_locator = MagicMock()
        table_locator.count.return_value = 1

        # Headers
        header_locators = MagicMock()
        header_locators.count.return_value = len(headers)
        header_locators.nth.side_effect = lambda i: _mock_cell(headers[i])
        table_locator.locator.side_effect = lambda sel: (
            header_locators if "thead" in sel or "th" in sel else _make_rows(rows)
        )

        page.locator.return_value = table_locator
        return page

    def test_extract_success(self) -> None:
        headers = ["Fecha", "Monto", "Descripcion"]
        rows_data = [["2024-01-01", "100.00", "Compra"], ["2024-01-02", "50.00", "Pago"]]
        page = _make_table_page_full(headers, rows_data)

        column_map = {"Fecha": "date", "Monto": "amount"}
        step = _step(
            table_selector="table",
            column_map=column_map,
        )
        extracted: list[dict[str, str]] = []
        execute_extract_table(page, step, extracted_rows=extracted)
        assert len(extracted) == 2
        assert extracted[0]["date"] == "2024-01-01"
        assert extracted[0]["amount"] == "100.00"

    def test_schema_mismatch_raises(self) -> None:
        headers = ["Fecha", "Monto"]
        page = _make_table_page_full(headers, [])
        step = _step(
            table_selector="table",
            column_map={"Fecha": "date", "NonExistent": "field"},
        )
        with pytest.raises(SchemaMismatch):
            execute_extract_table(page, step, extracted_rows=[])

    def test_table_not_found_raises(self) -> None:
        page = MagicMock()
        locator = MagicMock()
        locator.count.return_value = 0
        page.locator.return_value = locator
        step = _step(table_selector="table.missing", column_map={})
        with pytest.raises(SelectorNotFound):
            execute_extract_table(page, step, extracted_rows=[])

    def test_row_filter_applied(self) -> None:
        headers = ["Descripcion"]
        rows_data = [["transfer"], ["payment"], ["transfer fee"]]
        page = _make_table_page_full(headers, rows_data)
        step = _step(
            table_selector="table",
            column_map={"Descripcion": "desc"},
            row_filter="transfer",
        )
        extracted: list[dict[str, str]] = []
        execute_extract_table(page, step, extracted_rows=extracted)
        # Only rows containing "transfer" should pass
        assert all("transfer" in r["desc"] for r in extracted)


# ─────────────────────────────── select_date_range ───────────────────────────

class TestSelectDateRange:
    def _make_page(self, count: int = 1) -> MagicMock:
        page = MagicMock()
        locator = MagicMock()
        locator.count.return_value = count
        page.locator.return_value = locator
        return page

    def test_html_date_input_success(self) -> None:
        page = self._make_page()
        step = _step(
            from_selector="#from",
            to_selector="#to",
            from_date="2024-01-01",
            to_date="2024-01-31",
            widget="html-date-input",
        )
        execute_select_date_range(page, step)
        # fill called twice (from + to)
        assert page.locator.return_value.fill.call_count == 2

    def test_unsupported_widget_raises(self) -> None:
        page = self._make_page()
        step = _step(
            from_selector="#from",
            to_selector="#to",
            from_date="2024-01-01",
            to_date="2024-01-31",
            widget="some-custom-picker",
        )
        with pytest.raises(WidgetUnsupported):
            execute_select_date_range(page, step)

    def test_selector_not_found_raises(self) -> None:
        page = self._make_page(count=0)
        step = _step(
            from_selector="#from",
            to_selector="#to",
            from_date="2024-01-01",
            to_date="2024-01-31",
            widget="html-date-input",
        )
        with pytest.raises(SelectorNotFound):
            execute_select_date_range(page, step)


# ─────────────────────────────── download helpers ────────────────────────────

class TestWaitForDownload:
    def test_success_appends_bytes(self, tmp_path: pathlib.Path) -> None:

        # Create a real temp file for the download
        dl_file = tmp_path / "report.xlsx"
        dl_file.write_bytes(b"PK\x03\x04fake_xlsx_content")

        page = MagicMock()
        download = MagicMock()
        download.path.return_value = str(dl_file)

        ctx_mgr = MagicMock()
        ctx_mgr.__enter__ = MagicMock(return_value=ctx_mgr)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        ctx_mgr.value = download
        page.expect_download.return_value = ctx_mgr

        step = _step(trigger_selector="#download-btn", timeout_ms=5000)
        store: list[bytes] = []
        execute_wait_for_download(page, step, download_store=store)
        assert len(store) == 1
        assert store[0] == b"PK\x03\x04fake_xlsx_content"

    def test_timeout_raises(self) -> None:
        page = MagicMock()
        page.expect_download.side_effect = Exception("Timeout exceeded")
        step = _step(trigger_selector="#btn", timeout_ms=100)
        with pytest.raises(StepTimeout):
            execute_wait_for_download(page, step, download_store=[])

    def test_no_path_raises_download_failed(self) -> None:
        page = MagicMock()
        download = MagicMock()
        download.path.return_value = None

        ctx_mgr = MagicMock()
        ctx_mgr.__enter__ = MagicMock(return_value=ctx_mgr)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        ctx_mgr.value = download
        page.expect_download.return_value = ctx_mgr

        step = _step(trigger_selector="#btn")
        with pytest.raises(DownloadFailed):
            execute_wait_for_download(page, step, download_store=[])


class TestDownloadFile:
    def test_success_with_mime_validation(self, tmp_path: pathlib.Path) -> None:

        dl_file = tmp_path / "report.xlsx"
        dl_file.write_bytes(b"PK\x03\x04fake_xlsx")

        page = MagicMock()
        download = MagicMock()
        download.path.return_value = str(dl_file)
        download.suggested_filename = "report.xlsx"

        ctx_mgr = MagicMock()
        ctx_mgr.__enter__ = MagicMock(return_value=ctx_mgr)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        ctx_mgr.value = download
        page.expect_download.return_value = ctx_mgr

        step = _step(
            trigger_selector="#export",
            expected_mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            expected_extension=".xlsx",
        )
        store: list[bytes] = []
        execute_download_file(page, step, download_store=store)
        assert len(store) == 1

    def test_extension_mismatch_raises(self, tmp_path: pathlib.Path) -> None:

        dl_file = tmp_path / "report.pdf"
        dl_file.write_bytes(b"%PDFfake")

        page = MagicMock()
        download = MagicMock()
        download.path.return_value = str(dl_file)
        download.suggested_filename = "report.pdf"

        ctx_mgr = MagicMock()
        ctx_mgr.__enter__ = MagicMock(return_value=ctx_mgr)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        ctx_mgr.value = download
        page.expect_download.return_value = ctx_mgr

        step = _step(
            trigger_selector="#export",
            expected_mime="text/csv",
            expected_extension=".csv",
        )
        with pytest.raises(MimeMismatch):
            execute_download_file(page, step, download_store=[])

    def test_mime_magic_mismatch_raises(self, tmp_path: pathlib.Path) -> None:

        dl_file = tmp_path / "fake.xls"
        dl_file.write_bytes(b"NOTOLE2magic")  # wrong magic bytes

        page = MagicMock()
        download = MagicMock()
        download.path.return_value = str(dl_file)
        download.suggested_filename = "fake.xls"

        ctx_mgr = MagicMock()
        ctx_mgr.__enter__ = MagicMock(return_value=ctx_mgr)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        ctx_mgr.value = download
        page.expect_download.return_value = ctx_mgr

        step = _step(
            trigger_selector="#export",
            expected_mime="application/vnd.ms-excel",
            expected_extension=".xls",
        )
        with pytest.raises(MimeMismatch):
            execute_download_file(page, step, download_store=[])


# ─────────────────────────────── helper builders ─────────────────────────────

def _mock_cell(text: str) -> MagicMock:
    cell = MagicMock()
    cell.inner_text.return_value = text
    return cell


def _make_rows(rows: list[list[str]]) -> MagicMock:
    rows_locator = MagicMock()
    rows_locator.count.return_value = len(rows)

    def nth_row(i: int) -> MagicMock:
        row = MagicMock()
        row.inner_text.return_value = " ".join(rows[i])
        cells_loc = MagicMock()
        cells_loc.count.return_value = len(rows[i])
        cells_loc.nth.side_effect = lambda j: _mock_cell(rows[i][j])
        row.locator.return_value = cells_loc
        return row

    rows_locator.nth.side_effect = nth_row
    return rows_locator


def _make_table_page_full(
    headers: list[str], rows_data: list[list[str]]
) -> MagicMock:
    """Build a mock page with a fully functional table."""
    page = MagicMock()
    table_locator = MagicMock()
    table_locator.count.return_value = 1

    header_locators = MagicMock()
    header_locators.count.return_value = len(headers)
    header_locators.nth.side_effect = lambda i: _mock_cell(headers[i])

    rows_locator = _make_rows(rows_data)

    def table_sub_locator(sel: str) -> MagicMock:
        if "thead" in sel or "th" in sel:
            return header_locators
        return rows_locator

    table_locator.locator.side_effect = table_sub_locator
    page.locator.return_value = table_locator
    return page
