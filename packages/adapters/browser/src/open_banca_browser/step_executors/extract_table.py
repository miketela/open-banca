"""extract_table step executor."""
from __future__ import annotations

from typing import Any

from open_banca_browser.errors import SchemaMismatch, SelectorNotFound, StepTimeout
from open_banca_browser.step_executors._utils import extra
from open_banca_domain.entities.bank_map import StepSpec


def execute_extract_table(
    page: Any,
    step: StepSpec,
    *,
    extracted_rows: list[dict[str, str]],
) -> None:
    """Extract rows from an HTML table and append to extracted_rows.

    StepSpec extra fields:
        table_selector (str): CSS selector for the <table> element.
        column_map (dict[str, str]): Maps header text → field name in output.
            E.g. {"Fecha": "date", "Monto": "amount"}.
        row_filter (str | None): Optional substring filter on row text.

    Raises:
        SelectorNotFound: Table element not found.
        SchemaMismatch: A required column (from column_map) is absent in the table.
        StepTimeout: Could not access the table within timeout.
    """
    params = extra(step)
    table_selector: str = params.get("table_selector", "table")
    column_map: dict[str, str] = params.get("column_map", {})
    row_filter: str | None = params.get("row_filter", None)

    try:
        locator = page.locator(table_selector)
        count = locator.count()
        if count == 0:
            raise SelectorNotFound(
                f"extract_table: table not found: {table_selector!r}"
            )

        # Extract headers
        header_locators = locator.locator("thead tr th")
        header_count = header_locators.count()
        if header_count == 0:
            # Fallback: first <tr> in tbody may contain headers
            header_locators = locator.locator("tr:first-child th, tr:first-child td")
            header_count = header_locators.count()

        headers: list[str] = [
            header_locators.nth(i).inner_text(timeout=5_000).strip()
            for i in range(header_count)
        ]

        # Validate all required columns are present
        missing = [h for h in column_map if h not in headers]
        if missing:
            raise SchemaMismatch(
                f"extract_table: missing columns {missing!r} in {headers!r}"
            )

        # Build column index map
        col_indices = {col_name: headers.index(col_name) for col_name in column_map}

        # Extract data rows
        row_locators = locator.locator("tbody tr")
        row_count = row_locators.count()

        for i in range(row_count):
            row = row_locators.nth(i)
            row_text = row.inner_text(timeout=5_000)

            if row_filter and row_filter not in row_text:
                continue

            cells = row.locator("td")
            cell_count = cells.count()

            record: dict[str, str] = {}
            for header_text, field_name in column_map.items():
                idx = col_indices[header_text]
                if idx < cell_count:
                    record[field_name] = cells.nth(idx).inner_text(timeout=2_000).strip()
                else:
                    record[field_name] = ""

            extracted_rows.append(record)

    except (SelectorNotFound, SchemaMismatch):
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg:
            raise StepTimeout(f"extract_table timeout: {exc}") from exc
        raise StepTimeout(f"extract_table error: {exc}") from exc
