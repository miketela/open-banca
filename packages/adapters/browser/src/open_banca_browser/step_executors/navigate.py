"""navigate step executor."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from open_banca_browser.errors import HTTPError, StepTimeout
from open_banca_browser.step_executors._utils import extra
from open_banca_domain.entities.bank_map import StepSpec


def _resolve_url(url: str, secret_resolver: Callable[[str], str] | None) -> str:
    if secret_resolver is None or "<" not in url:
        return url
    resolved = url
    for placeholder in ("<USERNAME>", "<PASSWORD>", "<DATE_FROM>", "<DATE_TO>"):
        if placeholder in resolved:
            resolved = resolved.replace(placeholder, secret_resolver(placeholder))
    return resolved


def execute_navigate(
    page: Any,
    step: StepSpec,
    *,
    secret_resolver: Callable[[str], str] | None = None,
) -> None:
    """Navigate to a URL, waiting for the specified load state.

    StepSpec extra fields:
        url (str): Target URL.
        wait_until (str): 'load' | 'networkidle'. Defaults to 'load'.

    Raises:
        StepTimeout: Navigation exceeded 30 s.
        HTTPError: Server returned 4xx/5xx.
    """
    params = extra(step)
    url: str = _resolve_url(params.get("url", ""), secret_resolver)
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
