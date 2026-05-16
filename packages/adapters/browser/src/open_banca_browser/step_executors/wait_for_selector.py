"""wait_for_selector step executor."""

from __future__ import annotations

from typing import Any

from open_banca_browser.errors import SelectorNotFound, StepTimeout
from open_banca_browser.step_executors._utils import extra
from open_banca_domain.entities.bank_map import StepSpec

_VALID_STATES = {"visible", "attached", "hidden", "detached"}


def execute_wait_for_selector(page: Any, step: StepSpec) -> None:
    """Wait until a DOM element reaches a given state.

    StepSpec extra fields:
        selector (str): CSS/XPath selector.
        state (str): 'visible' | 'attached' | 'hidden'. Defaults to 'visible'.
        timeout_ms (int): Max wait in milliseconds. Defaults to 15000.

    Raises:
        SelectorNotFound: Element never appeared (state=visible|attached).
        StepTimeout: Timeout exceeded before state was reached.
    """
    params = extra(step)
    selector: str = params.get("selector", "")
    state: str = params.get("state", "visible")
    timeout_ms: int = int(params.get("timeout_ms", 15_000))

    if state not in _VALID_STATES:
        state = "visible"

    try:
        page.wait_for_selector(selector, state=state, timeout=timeout_ms)
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg:
            raise StepTimeout(f"wait_for_selector timeout: {selector!r} state={state}") from exc
        raise SelectorNotFound(f"wait_for_selector: {selector!r} not found") from exc
