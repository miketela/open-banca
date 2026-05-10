"""click step executor."""
from __future__ import annotations

from typing import Any

from open_banca_browser.errors import SelectorNotFound, StepTimeout
from open_banca_browser.step_executors._utils import extra
from open_banca_domain.entities.bank_map import StepSpec


def execute_click(page: Any, step: StepSpec) -> None:
    """Click a DOM element identified by a CSS selector.

    StepSpec extra fields:
        selector (str): CSS/XPath selector.
        nth (int): Optional 0-based index when selector matches multiple elements.

    Raises:
        SelectorNotFound: Element is not present in the DOM.
        StepTimeout: Element exists but is not clickable within timeout.
    """
    params = extra(step)
    selector: str = params.get("selector", "")
    nth: int | None = params.get("nth", None)

    try:
        if nth is not None:
            locator = page.locator(selector).nth(nth)
        else:
            locator = page.locator(selector)

        # Check existence first for a cleaner error
        count = locator.count()
        if count == 0:
            raise SelectorNotFound(f"click: selector not found: {selector!r}")

        locator.click(timeout=15_000)
    except SelectorNotFound:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg:
            raise StepTimeout(f"click timeout on {selector!r}") from exc
        if "not found" in msg or "no element" in msg or "strict mode" in msg:
            raise SelectorNotFound(f"click: selector not found: {selector!r}") from exc
        raise StepTimeout(f"click error on {selector!r}: {exc}") from exc
