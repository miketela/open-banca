"""ParseExcelActivity — parse a bank Excel file into normalized Transaction records.

Retry policy (orchestrator.md §Inventario):
  - 1 attempt only (parser is deterministic — no value in retrying).
  - start-to-close timeout: 60 s.
  - NO heartbeat (sync, threadpool execution).

Idempotency key: SHA-256 hash of the Excel file content.

Runs in a ThreadPoolExecutor because openpyxl is synchronous blocking I/O.
Worker registers this as a sync activity with executor=thread_pool_executor.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field
from temporalio import activity
from temporalio.exceptions import ApplicationError

from open_banca_domain.entities.bank_map import ParserConfig as DomainParserConfig
from open_banca_parsing.engine import ExcelParseError, ExcelParser

_BANKS_DIR = Path(__file__).resolve().parents[5] / "banks"


class ParserConfig(BaseModel):
    """Parser configuration reference — bank_id selects packages/banks/{bank_id}/parser.json."""

    bank_id: str = Field(description="Bank identifier to select the correct parser DSL")
    parser_version: str = Field(
        default="1.0",
        description="Parser DSL version for forward compatibility",
    )


class TransactionRecord(BaseModel):
    """Normalized transaction record output by the parser."""

    raw_id: str = Field(description="Bank-native transaction ID embedded in the Excel row")
    date: str = Field(description="ISO 8601 date string (YYYY-MM-DD)")
    description: str = Field(description="Transaction description from the bank")
    amount: str = Field(description="Decimal amount as string to preserve precision")
    currency: str = Field(default="USD", description="ISO 4217 currency code")
    account_id: str = Field(description="Account the transaction belongs to")
    fingerprint_hash: str = Field(
        default="",
        description="SHA-256 dedup fingerprint from parser engine",
    )
    extra: dict[str, str] = Field(
        default_factory=dict,
        description="Additional bank-specific fields preserved for dedup fingerprinting",
    )


class ParseExcelInput(BaseModel):
    """Input for ParseExcelActivity."""

    excel_path: str = Field(
        description="Path to the Excel file inside the sandbox container"
    )
    content_hash: str = Field(
        description="SHA-256 hex digest for idempotency — if already parsed, return cached"
    )
    account_id: str = Field(description="Account context for transaction records")
    parser_config: ParserConfig = Field(description="DSL parser configuration")


class ParseExcelResult(BaseModel):
    """Result from ParseExcelActivity."""

    transactions: list[TransactionRecord] = Field(
        description="Normalized transaction records parsed from the Excel file"
    )
    row_count: int = Field(description="Total rows processed (including headers)")
    skipped_rows: int = Field(
        default=0, description="Rows skipped due to parse errors"
    )


class ParseExcelActivity:
    """ParseExcelActivity class-based wrapper."""


def _load_parser_config(bank_id: str) -> DomainParserConfig:
    parser_path = _BANKS_DIR / bank_id / "parser.json"
    if not parser_path.exists():
        raise ApplicationError(
            f"parser.json not found for bank_id={bank_id!r}",
            non_retryable=True,
            type="parser_not_found",
        )
    data = json.loads(parser_path.read_text(encoding="utf-8"))
    return DomainParserConfig(**data)


def _domain_txn_to_record(txn: object, fallback_account_id: str) -> TransactionRecord:
    from open_banca_domain.entities.transaction import Transaction

    assert isinstance(txn, Transaction)
    account_id = txn.account_id if txn.account_id != "unknown" else fallback_account_id
    extra: dict[str, str] = {}
    if txn.embedded_id:
        extra["embedded_id"] = txn.embedded_id
    return TransactionRecord(
        raw_id=txn.id,
        date=txn.posted_at.date().isoformat(),
        description=txn.description,
        amount=str(txn.amount),
        currency=txn.currency,
        account_id=account_id,
        fingerprint_hash=txn.fingerprint_hash,
        extra=extra,
    )


@activity.defn(name="ParseExcelActivity")
def parse_excel(input: ParseExcelInput) -> ParseExcelResult:  # noqa: A002
    """Parse the bank Excel file using the DSL parser engine."""
    excel_path = Path(input.excel_path)
    if not excel_path.exists():
        raise ApplicationError(
            f"Excel file not found: {input.excel_path}",
            non_retryable=True,
            type="excel_not_found",
        )

    try:
        workbook_bytes = excel_path.read_bytes()
    except OSError as exc:
        raise ApplicationError(
            f"Cannot read Excel file: {exc}",
            non_retryable=True,
            type="excel_read_failed",
        ) from exc

    parser_config = _load_parser_config(input.parser_config.bank_id)
    engine = ExcelParser()

    try:
        transactions = engine.parse(workbook_bytes, parser_config)
    except ExcelParseError as exc:
        raise ApplicationError(
            str(exc),
            non_retryable=True,
            type=exc.code,
        ) from exc

    records = [
        _domain_txn_to_record(txn, input.account_id) for txn in transactions
    ]

    activity.logger.info(
        "parse_excel complete: path=%s transactions=%d",
        input.excel_path,
        len(records),
    )

    return ParseExcelResult(
        transactions=records,
        row_count=len(records),
        skipped_rows=0,
    )
