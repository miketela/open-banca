"""assert_text step executor."""

from __future__ import annotations

import re
from typing import Any

from open_banca_browser.errors import AssertionFailed, SelectorNotFound, StepTimeout
from open_banca_browser.step_executors._utils import extra
from open_banca_domain.entities.bank_map import StepSpec

_VALID_MODES = {"exact", "contains", "regex"}


def execute_assert_text(page: Any, step: StepSpec) -> None:
    """Assert that a DOM element's text matches the expected value.

    StepSpec extra fields:
        selector (str): CSS selector for the element to inspect.
        expected (str): Expected text (literal or regex pattern).
        mode (str): 'exact' | 'contains' | 'regex'. Defaults to 'contains'.

    Raises:
        SelectorNotFound: Element not found in the DOM.
        AssertionFailed: Text does not match expectation (indicates content drift).
        StepTimeout: Could not retrieve text within timeout.
    """
    params = extra(step)
    selector: str = params.get("selector", "")
    expected: str = params.get("expected", "")
    mode: str = params.get("mode", "contains")

    if mode not in _VALID_MODES:
        mode = "contains"

    try:
        locator = page.locator(selector)
        count = locator.count()
        if count == 0:
            raise SelectorNotFound(f"assert_text: selector not found: {selector!r}")
        actual: str = locator.inner_text(timeout=10_000).strip()
    except SelectorNotFound:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg:
            raise StepTimeout(f"assert_text timeout on {selector!r}") from exc
        raise SelectorNotFound(f"assert_text: selector not found: {selector!r}") from exc

    match = False
    if mode == "exact":
        match = actual == expected
    elif mode == "contains":
        match = expected in actual
    elif mode == "regex":
        match = bool(re.search(expected, actual))

    if not match:
        raise AssertionFailed(
            f"assert_text failed [{mode}]: expected={expected!r}, actual={actual!r}"
        )
