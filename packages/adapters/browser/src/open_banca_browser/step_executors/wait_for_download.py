"""wait_for_download step executor."""

from __future__ import annotations

from typing import Any

from open_banca_browser.errors import DownloadFailed, StepTimeout
from open_banca_browser.step_executors._utils import extra
from open_banca_domain.entities.bank_map import StepSpec


def execute_wait_for_download(
    page: Any,
    step: StepSpec,
    *,
    download_store: list[bytes],
) -> None:
    """Click a trigger element and wait for a file download event.

    Captured file bytes are appended to download_store (index 0 = first captured).

    StepSpec extra fields:
        trigger_selector (str): CSS selector for the element that initiates download.
        timeout_ms (int): Max wait in milliseconds. Defaults to 60000.

    Raises:
        StepTimeout: Download event not received within timeout.
        DownloadFailed: Download was aborted before completion.
    """
    params = extra(step)
    trigger_selector: str = params.get("trigger_selector", "")
    timeout_ms: int = int(params.get("timeout_ms", 60_000))

    try:
        with page.expect_download(timeout=timeout_ms) as download_info:
            page.locator(trigger_selector).click()
        download = download_info.value
        # Read file bytes into memory
        path = download.path()
        if path is None:
            raise DownloadFailed("wait_for_download: download aborted — no path")
        with open(path, "rb") as fh:
            data = fh.read()
        download_store.append(data)
    except (DownloadFailed, StepTimeout):
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg:
            raise StepTimeout(
                f"wait_for_download timeout waiting for download from {trigger_selector!r}"
            ) from exc
        raise DownloadFailed(f"wait_for_download aborted: {exc}") from exc
