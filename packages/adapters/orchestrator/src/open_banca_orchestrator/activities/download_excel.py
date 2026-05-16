"""DownloadExcelActivity — trigger and retrieve the Excel export from the bank portal.

Retry policy (orchestrator.md §Inventario):
  - Exponential backoff, 3 attempts.
  - start-to-close timeout: 2 min.
  - Heartbeat every 10 s during download.

Idempotency key: job_id + account_id + period (so replay re-uses cached file).

IMPLEMENTATION STATUS: skeleton — raises NotImplementedError.
Real implementation lands in the task that wires up the Playwright Runner.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field
from temporalio import activity

from open_banca_orchestrator.activities.login import BrowserSessionToken


class DownloadPeriod(BaseModel):
    """Date range for the Excel export request."""

    since: datetime.date = Field(description="Start date (inclusive)")
    until: datetime.date = Field(description="End date (inclusive)")


class DownloadExcelInput(BaseModel):
    """Input for DownloadExcelActivity."""

    job_id: str = Field(description="Unique job identifier")
    account_id: str = Field(description="Account identifier")
    period: DownloadPeriod = Field(description="Date range to download")
    bank_id: str = Field(description="Bank identifier to locate download step in map.json")
    browser_session_token: BrowserSessionToken = Field(
        description="Active sidecar session token (ADR-0019)"
    )


class DownloadExcelResult(BaseModel):
    """Result from DownloadExcelActivity."""

    excel_path: str = Field(
        description="Path to the downloaded Excel file inside the sandbox container"
    )
    file_size_bytes: int = Field(description="File size for validation")
    content_hash: str = Field(
        description="SHA-256 hex digest of the file content for idempotency checks"
    )


class DownloadExcelActivity:
    """DownloadExcelActivity class-based wrapper."""


@activity.defn(name="DownloadExcelActivity")
async def download_excel(input: DownloadExcelInput) -> DownloadExcelResult:
    """Click the export button, wait for download, and return the file path.

    Sends heartbeats every 10 s during the download wait.

    TODO: implement Excel export click sequence from map.json.
    TODO: wait for download event from Playwright, move to deterministic path.
    TODO: call activity.heartbeat(bytes_downloaded=N) during download.
    """
    raise NotImplementedError(
        "DownloadExcelActivity not implemented — wired in task that implements Playwright Runner"
    )
