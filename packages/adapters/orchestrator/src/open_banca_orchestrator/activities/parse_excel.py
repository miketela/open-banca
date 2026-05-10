"""ParseExcelActivity — parse a bank Excel file into normalized Transaction records.

Retry policy (orchestrator.md §Inventario):
  - 1 attempt only (parser is deterministic — no value in retrying).
  - start-to-close timeout: 60 s.
  - NO heartbeat (sync, threadpool execution).

Idempotency key: SHA-256 hash of the Excel file content.

Runs in a ThreadPoolExecutor because openpyxl is synchronous blocking I/O.
Worker registers this as a sync activity with executor=thread_pool_executor.

IMPLEMENTATION STATUS: skeleton — raises NotImplementedError.
Real implementation lands in task 17 (Excel parsing engine).
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from temporalio import activity


class ParserConfig(BaseModel):
    """Parser configuration loaded from parser.json for the specific bank."""

    bank_id: str = Field(description="Bank identifier to select the correct parser DSL")
    parser_version: str = Field(
        default="1.0",
        description="Parser DSL version for forward compatibility",
    )


class TransactionRecord(BaseModel):
    """Normalized transaction record output by the parser.

    Maps to the Transaction domain entity; exact field set defined in domain package.
    Using a local model here to avoid circular dependency at skeleton stage.
    """

    raw_id: str = Field(description="Bank-native transaction ID embedded in the Excel row")
    date: str = Field(description="ISO 8601 date string (YYYY-MM-DD)")
    description: str = Field(description="Transaction description from the bank")
    amount: str = Field(description="Decimal amount as string to preserve precision")
    currency: str = Field(default="USD", description="ISO 4217 currency code")
    account_id: str = Field(description="Account the transaction belongs to")
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


@activity.defn(name="ParseExcelActivity")
def parse_excel(input: ParseExcelInput) -> ParseExcelResult:  # noqa: A002
    """Parse the bank Excel file using the DSL parser engine (task 17).

    Synchronous function — runs in ThreadPoolExecutor on the worker.
    No heartbeat needed (deterministic, bounded execution).

    TODO: integrate with openpyxl + DSL parsing engine (task 17).
    TODO: implement 3-level dedup ID extraction from Excel rows.
    TODO: handle Banistmo-style IDs embedded in Detail column.
    """
    raise NotImplementedError(
        "ParseExcelActivity not implemented — wired in task 17 (Excel parsing engine)"
    )
