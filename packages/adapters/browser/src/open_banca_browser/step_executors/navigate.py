"""navigate step executor."""

from __future__ import annotations

from typing import Any

from open_banca_browser.errors import HTTPError, StepTimeout
from open_banca_browser.step_executors._utils import extra
from open_banca_domain.entities.bank_map import StepSpec


def execute_navigate(page: Any, step: StepSpec) -> None:
    """Navigate to a URL, waiting for the specified load state.

    StepSpec extra fields:
        url (str): Target URL.
        wait_until (str): 'load' | 'networkidle'. Defaults to 'load'.

    Raises:
        StepTimeout: Navigation exceeded 30 s.
        HTTPError: Server returned 4xx/5xx.
    """
    params = extra(step)
    url: str = params.get("url", "")
    wait_until: str = params.get("wait_until", "load")

    try:
        response = page.goto(url, wait_until=wait_until, timeout=30_000)
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg:
            raise StepTimeout(f"navigate timeout to {url}") from exc
        raise StepTimeout(f"navigate error: {exc}") from exc

    if response is not None:
        status: int = response.status
        if status >= 400:
            raise HTTPError(
                f"navigate got HTTP {status} for {url}",
                http_status=status,
            )
