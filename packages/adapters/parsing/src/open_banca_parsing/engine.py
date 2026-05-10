"""engine.py — ExcelParser: implements ExcelParserPort with DSL-driven hardened parsing.

Security constraints (ADR-0007-amendment):
1. defusedxml.lxml patched BEFORE openpyxl import — prevents XXE / billion-laughs.
2. File size guard: reject .xlsx bytes > 50 MB.
3. ZIP ratio guard: reject if any entry's uncompressed/compressed ratio > 100x
   (zip-bomb heuristic, CVE-2017-5992).
4. Sheet data guard: reject if uncompressed cell data > 10 MB per sheet.

These guards are irrechazable — no runtime flag disables them.
"""
from __future__ import annotations

# MANDATORY: patch defusedxml BEFORE any openpyxl import
import defusedxml
import defusedxml.ElementTree

defusedxml.defuse_stdlib()  # type: ignore[attr-defined]

import io  # noqa: E402
import zipfile  # noqa: E402
from datetime import UTC  # noqa: E402
from decimal import Decimal  # noqa: E402
from typing import Any  # noqa: E402

import openpyxl  # noqa: E402

from open_banca_domain.entities.bank_map import ParserConfig  # noqa: E402
from open_banca_domain.entities.transaction import Transaction  # noqa: E402
from open_banca_parsing.dsl.evaluator import apply_transform_spec  # noqa: E402
from open_banca_parsing.parser_config import (  # noqa: E402
    DataEndMarker,
    IdStrategy,
    ParserSpec,
    SheetSpec,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB
_MAX_ZIP_RATIO = 100  # compressed:uncompressed ratio cap
_MAX_SHEET_BYTES = 10 * 1024 * 1024  # 10 MB uncompressed per sheet
_MAX_ROWS_PER_SHEET = 10_000


class ExcelParseError(ValueError):
    """Raised for all engine-level parse failures."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")


# ---------------------------------------------------------------------------
# ZIP-level security guards
# ---------------------------------------------------------------------------


def _check_zip_bomb(data: bytes) -> None:
    """Scan ZIP entries for bomb heuristic without fully decompressing.

    Raises:
        ExcelParseError: If any entry's compress_size is 0 (avoid div-by-zero) and
                         file_size exceeds 50 MB, or if ratio > _MAX_ZIP_RATIO.
    """
    buf = io.BytesIO(data)
    try:
        with zipfile.ZipFile(buf, "r") as zf:
            for info in zf.infolist():
                compress_size = info.compress_size
                file_size = info.file_size
                # Skip empty entries
                if compress_size == 0:
                    if file_size > _MAX_FILE_BYTES:
                        raise ExcelParseError(
                            "excel_zip_bomb_heuristic",
                            f"Entry {info.filename!r} has compress_size=0 and "
                            f"file_size={file_size} exceeds limit",
                        )
                    continue
                ratio = file_size / compress_size
                if ratio > _MAX_ZIP_RATIO:
                    raise ExcelParseError(
                        "excel_zip_bomb_heuristic",
                        f"Entry {info.filename!r} has zip ratio {ratio:.1f}x "
                        f"(limit {_MAX_ZIP_RATIO}x). Possible zip bomb.",
                    )
    except zipfile.BadZipFile as exc:
        raise ExcelParseError("excel_invalid_zip", f"Not a valid ZIP/xlsx file: {exc}") from exc


# ---------------------------------------------------------------------------
# Column resolution helpers
# ---------------------------------------------------------------------------


def _col_letter_to_index(letter: str) -> int:
    """Convert Excel column letter (A, B, AA…) to 0-based index."""
    result = 0
    for ch in letter.upper():
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result - 1


def _resolve_col_index(source: str, header_map: dict[str, int]) -> int:
    """Resolve column source (header name or letter) to 0-based column index."""
    if source in header_map:
        return header_map[source]
    # Try as column letter
    try:
        return _col_letter_to_index(source)
    except Exception as exc:
        msg = f"Cannot resolve column {source!r}: not in header map and not a valid letter"
        raise ExcelParseError("column_not_found", msg) from exc


def _get_cell_value(row: tuple[Any, ...], col_idx: int) -> str:
    """Extract cell value from a row tuple as string."""
    if col_idx < 0 or col_idx >= len(row):
        return ""
    cell = row[col_idx]
    if cell is None:
        return ""
    return str(cell)


# ---------------------------------------------------------------------------
# Transform pipeline application
# ---------------------------------------------------------------------------


def _apply_pipeline(
    value: str,
    transforms: list[Any],
    row: tuple[Any, ...],
    header_map: dict[str, int],
) -> Any:
    """Apply a list of transform specs to an initial *value*.

    Multi-ref transforms (coalesce, concat) resolve their refs from the row.
    """
    current: Any = value
    for t in transforms:
        spec = t.model_dump() if hasattr(t, "model_dump") else dict(t)
        helper = spec.get("helper")

        if helper == "coalesce":
            # Resolve all refs and find the first non-empty
            refs: list[str] = spec.get("refs", [])
            values = [_get_cell_value(row, _resolve_col_index(r, header_map)) for r in refs]
            from open_banca_parsing.dsl.helpers import coalesce as _coalesce
            current = _coalesce(*values)
        elif helper == "concat":
            refs_c: list[str] = spec.get("refs", [])
            values_c = [_get_cell_value(row, _resolve_col_index(r, header_map)) for r in refs_c]
            from open_banca_parsing.dsl.helpers import concat as _concat
            current = _concat(*values_c, sep=spec.get("sep", " "))
        else:
            current = apply_transform_spec(spec, str(current))
    return current


# ---------------------------------------------------------------------------
# ID computation
# ---------------------------------------------------------------------------


def _compute_id(
    id_strategy: IdStrategy,
    row_record: dict[str, Any],
    embedded_raw: str | None,
) -> str:
    """Derive a stable transaction ID from the row record and id_strategy."""
    import hashlib

    if id_strategy.strategy == "embedded" and embedded_raw:
        return embedded_raw.strip()

    # fingerprint strategy
    fields = id_strategy.fingerprint_fields or list(row_record.keys())
    parts = [str(row_record.get(f, "")) for f in fields]
    fingerprint = "|".join(parts)
    return hashlib.sha256(fingerprint.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Sheet metadata extraction
# ---------------------------------------------------------------------------


def _extract_metadata(
    ws: Any,
    spec: SheetSpec,
) -> dict[str, str]:
    """Extract account metadata from absolute cell addresses."""
    meta: dict[str, str] = {}
    for cell_addr, field_name in spec.account_metadata_extraction.items():
        try:
            cell = ws[cell_addr]
            meta[field_name] = str(cell.value) if cell.value is not None else ""
        except Exception:
            meta[field_name] = ""
    return meta


# ---------------------------------------------------------------------------
# Main ExcelParser
# ---------------------------------------------------------------------------


class ExcelParser:
    """Implements ExcelParserPort — DSL-driven Excel parsing with security hardening."""

    def parse(self, workbook_bytes: bytes, parser_spec: ParserConfig) -> list[Transaction]:
        """Parse *workbook_bytes* using the DSL configuration in *parser_spec*.

        Args:
            workbook_bytes: Raw .xlsx file bytes.
            parser_spec: Domain ParserConfig (extra="allow") carrying the full DSL JSON.

        Returns:
            List of Transaction entities.

        Raises:
            ExcelParseError: On any security violation or parse failure.
        """
        # --- Guard 1: file size ---
        if len(workbook_bytes) > _MAX_FILE_BYTES:
            raise ExcelParseError(
                "excel_too_large",
                f"File is {len(workbook_bytes)} bytes, limit is {_MAX_FILE_BYTES} (50 MB)",
            )

        # --- Guard 2: ZIP bomb heuristic ---
        _check_zip_bomb(workbook_bytes)

        # --- Load parser spec ---
        spec_dict = parser_spec.model_dump()
        parser = ParserSpec.from_dict(spec_dict)

        # --- Open workbook (defusedxml already patched at module import) ---
        buf = io.BytesIO(workbook_bytes)
        try:
            wb = openpyxl.load_workbook(buf, read_only=True, data_only=True)
        except Exception as exc:
            raise ExcelParseError("excel_open_failed", f"Cannot open workbook: {exc}") from exc

        transactions: list[Transaction] = []

        import re2 as _re2  # type: ignore[import-untyped]

        for sheet_spec in parser.sheets:
            # Match sheet by name_pattern
            matched_ws = None
            for name in wb.sheetnames:
                try:
                    if _re2.fullmatch(sheet_spec.name_pattern, name):
                        matched_ws = wb[name]
                        break
                except Exception:
                    continue

            if matched_ws is None:
                continue  # Skip unmatched sheets (not an error per spec)

            txns = self._parse_sheet(matched_ws, sheet_spec, parser)
            transactions.extend(txns)

        wb.close()
        return transactions

    def _parse_sheet(
        self,
        ws: Any,
        spec: SheetSpec,
        parser: ParserSpec,
    ) -> list[Transaction]:
        """Parse a single matched worksheet into Transaction records."""

        all_rows = list(ws.iter_rows(values_only=True))

        # Guard 3: sheet data size (approximate — count chars)
        sheet_bytes = sum(
            sum(len(str(cell)) for cell in row if cell is not None)
            for row in all_rows
        )
        if sheet_bytes > _MAX_SHEET_BYTES:
            raise ExcelParseError(
                "sheet_too_large",
                f"Sheet data {sheet_bytes} bytes exceeds {_MAX_SHEET_BYTES} (10 MB)",
            )

        # Extract header row (1-based → 0-based)
        header_row_idx = spec.header_row - 1
        if header_row_idx >= len(all_rows):
            raise ExcelParseError("header_row_missing", "Header row index out of range")

        header_row = all_rows[header_row_idx]
        header_map: dict[str, int] = {}
        for idx, cell in enumerate(header_row):
            if cell is not None:
                header_map[str(cell).strip()] = idx

        # Extract metadata (before iterating data rows)
        _metadata = _extract_metadata(ws, spec)
        account_id = _metadata.get("account_id", _metadata.get("account_number", "unknown"))

        data_start_idx = spec.data_start_row - 1
        transactions: list[Transaction] = []
        row_count = 0

        for row in all_rows[data_start_idx:]:
            # Guard 4: max rows per sheet
            if row_count >= _MAX_ROWS_PER_SHEET:
                raise ExcelParseError(
                    "sheet_too_many_rows",
                    f"Sheet exceeds {_MAX_ROWS_PER_SHEET} rows limit",
                )

            # End marker check
            if spec.data_end_marker == DataEndMarker.EMPTY_ROW:
                if all(cell is None or str(cell).strip() == "" for cell in row):
                    break

            row_record: dict[str, Any] = {}
            for col_entry in spec.column_map:
                try:
                    col_idx = _resolve_col_index(col_entry.source, header_map)
                except ExcelParseError:
                    col_idx = -1

                raw_value = _get_cell_value(row, col_idx)

                if not raw_value and col_entry.required:
                    if col_entry.on_missing.value == "fail":
                        raise ExcelParseError(
                            "required_column_missing",
                            f"Required column {col_entry.source!r} is empty in row {data_start_idx + row_count + 1}",
                        )
                    elif col_entry.on_missing.value == "default" and col_entry.default_value is not None:
                        raw_value = col_entry.default_value
                    else:
                        raw_value = ""

                try:
                    transformed = _apply_pipeline(
                        raw_value,
                        col_entry.transformations,
                        row,
                        header_map,
                    )
                except Exception as exc:
                    if col_entry.required and col_entry.on_missing.value == "fail":
                        raise ExcelParseError(
                            "transform_failed",
                            f"Transform failed for {col_entry.target_field!r}: {exc}",
                        ) from exc
                    transformed = raw_value

                row_record[col_entry.target_field] = transformed

            # Build transaction
            txn = self._build_transaction(
                row_record=row_record,
                account_id=account_id,
                id_strategy=spec.id_strategy,
                currency=parser.defaults.currency,
                row_idx=data_start_idx + row_count,
            )
            if txn is not None:
                transactions.append(txn)

            row_count += 1

        return transactions

    def _build_transaction(
        self,
        row_record: dict[str, Any],
        account_id: str,
        id_strategy: IdStrategy,
        currency: str,
        row_idx: int,
    ) -> Transaction | None:
        """Construct a Transaction domain entity from a parsed row record."""
        import hashlib
        from datetime import datetime

        # Required fields
        date_val = row_record.get("date") or row_record.get("posted_at")
        amount_val = row_record.get("amount")
        description_val = str(row_record.get("description", ""))

        if date_val is None:
            return None  # Skip rows without date

        # Normalise date
        if isinstance(date_val, str):
            try:
                from open_banca_parsing.dsl.helpers import parse_date as _pd
                date_val = _pd(date_val, "%Y-%m-%dT%H:%M:%S")
            except Exception:
                return None

        if not isinstance(date_val, datetime):
            return None

        # Ensure timezone-aware
        if date_val.tzinfo is None:
            date_val = date_val.replace(tzinfo=UTC)

        # Normalise amount
        if amount_val is None:
            return None
        if not isinstance(amount_val, Decimal):
            try:
                from open_banca_parsing.dsl.helpers import normalize_amount as _na
                amount_val = _na(str(amount_val))
            except Exception:
                return None

        # Compute fingerprint hash
        fingerprint_parts = [account_id, str(date_val.isoformat()), str(amount_val), description_val]
        fingerprint_hash = hashlib.sha256("|".join(fingerprint_parts).encode()).hexdigest()

        # Compute ID
        embedded_raw = str(row_record.get("embedded_id", "")) or None
        txn_id = _compute_id(id_strategy, row_record, embedded_raw)

        currency_val = str(row_record.get("currency", currency))

        return Transaction(
            id=txn_id,
            account_id=account_id,
            posted_at=date_val,
            value_at=date_val,
            amount=amount_val,
            currency=currency_val,
            description=description_val,
            fingerprint_hash=fingerprint_hash,
            embedded_id=embedded_raw,
        )
