"""download_file step executor."""

from __future__ import annotations

from typing import Any

from open_banca_browser.errors import DownloadFailed, MimeMismatch, StepTimeout
from open_banca_browser.step_executors._utils import extra
from open_banca_domain.entities.bank_map import StepSpec


def execute_download_file(
    page: Any,
    step: StepSpec,
    *,
    download_store: list[bytes],
) -> None:
    """Trigger a file download and validate MIME type and extension.

    Captured file bytes are appended to download_store.

    StepSpec extra fields:
        trigger_selector (str): CSS selector for the download trigger.
        expected_mime (str): Expected MIME type, e.g. 'application/vnd.ms-excel'.
        expected_extension (str): Expected file extension, e.g. '.xlsx'.
        timeout_ms (int): Max wait in milliseconds. Defaults to 60000.

    Raises:
        StepTimeout: Download event not received within timeout.
        DownloadFailed: Download aborted before completion.
        MimeMismatch: File has wrong MIME type or extension.
    """
    params = extra(step)
    trigger_selector: str = params.get("trigger_selector", "")
    expected_mime: str = params.get("expected_mime", "")
    expected_extension: str = params.get("expected_extension", "")
    timeout_ms: int = int(params.get("timeout_ms", 60_000))

    try:
        with page.expect_download(timeout=timeout_ms) as download_info:
            page.locator(trigger_selector).click()
        download = download_info.value

        # Validate filename extension
        suggested_name: str = download.suggested_filename
        if expected_extension and not suggested_name.endswith(expected_extension):
            raise MimeMismatch(
                f"download_file: extension mismatch. "
                f"Expected {expected_extension!r}, got {suggested_name!r}"
            )

        path = download.path()
        if path is None:
            raise DownloadFailed("download_file: download aborted — no path returned")

        with open(path, "rb") as fh:
            data = fh.read()

        # Validate MIME by sniffing magic bytes when expected_mime is provided
        if expected_mime:
            _validate_mime(data, expected_mime, suggested_name)

        download_store.append(data)

    except (DownloadFailed, MimeMismatch, StepTimeout):
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg:
            raise StepTimeout(f"download_file timeout for trigger {trigger_selector!r}") from exc
        raise DownloadFailed(f"download_file failed: {exc}") from exc


def _validate_mime(data: bytes, expected_mime: str, filename: str) -> None:
    """Basic magic-byte MIME validation without external dependencies."""
    # Map common MIME types to their magic bytes
    magic_map: dict[str, bytes] = {
        "application/vnd.ms-excel": b"\xd0\xcf\x11\xe0",  # OLE2 (xls)
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": b"PK\x03\x04",  # xlsx (zip)
        "text/csv": b"",  # no reliable magic; skip
        "application/pdf": b"%PDF",
        "application/zip": b"PK\x03\x04",
    }

    magic = magic_map.get(expected_mime)
    if magic is None or magic == b"":
        # Unknown or un-checkable MIME type — skip magic validation
        return

    if not data.startswith(magic):
        raise MimeMismatch(
            f"download_file: MIME magic mismatch for {filename!r}. "
            f"Expected {expected_mime!r} magic bytes."
        )
