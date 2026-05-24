"""wait_for_selector step executor."""

from __future__ import annotations

from typing import Any

from open_banca_browser.errors import SelectorNotFound, StepTimeout
from open_banca_browser.step_executors._utils import extra, root_locator
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

    frame_selector: str = params.get("frame_selector") or params.get("iframe_selector") or ""
    nth: int | None = params.get("nth", None)
    try:
        if frame_selector:
            loc = root_locator(page, step).locator(selector)
            if nth is not None:
                loc = loc.nth(nth)
            else:
                loc = loc.first
            loc.wait_for(state=state, timeout=timeout_ms)
        else:
            page.wait_for_selector(selector, state=state, timeout=timeout_ms)
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg:
            raise StepTimeout(f"wait_for_selector timeout: {selector!r} state={state}") from exc
        raise SelectorNotFound(f"wait_for_selector: {selector!r} not found") from exc
