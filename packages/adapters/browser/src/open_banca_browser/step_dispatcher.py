"""Step dispatcher — maps action strings to executor callables."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from open_banca_browser.step_executors.assert_text import execute_assert_text
from open_banca_browser.step_executors.click import execute_click
from open_banca_browser.step_executors.download_file import execute_download_file
from open_banca_browser.step_executors.extract_table import execute_extract_table
from open_banca_browser.step_executors.fill import execute_fill
from open_banca_browser.step_executors.navigate import execute_navigate
from open_banca_browser.step_executors.prompt_user import execute_prompt_user
from open_banca_browser.step_executors.select_date_range import execute_select_date_range
from open_banca_browser.step_executors.wait_for_download import execute_wait_for_download
from open_banca_browser.step_executors.wait_for_selector import execute_wait_for_selector
from open_banca_domain.entities.bank_map import StepSpec

# Type alias for the executor signature stored in the table.
# Each executor receives (page, step, **kwargs) where kwargs are runner-level
# shared state (secret_resolver, download_store, extracted_rows).
StepExecutor = Callable[..., None]

#: Lookup table: action string → executor function.
STEP_DISPATCH_TABLE: dict[str, StepExecutor] = {
    "navigate": execute_navigate,
    "click": execute_click,
    "fill": execute_fill,
    "wait_for_selector": execute_wait_for_selector,
    "wait_for_download": execute_wait_for_download,
    "select_date_range": execute_select_date_range,
    "assert_text": execute_assert_text,
    "extract_table": execute_extract_table,
    "download_file": execute_download_file,
    "prompt_user": execute_prompt_user,
}


def dispatch_step(
    page: Any,
    step: StepSpec,
    **kwargs: Any,
) -> None:
    """Dispatch a single step to its executor.

    Args:
        page: Playwright Page object.
        step: The StepSpec to execute.
        **kwargs: Passed through to the executor (secret_resolver, download_store, etc.).

    Raises:
        KeyError: Unknown action (programming error — map validation should catch this).
    """
    executor = STEP_DISPATCH_TABLE[step.action]
    params = inspect.signature(executor).parameters
    filtered = {k: v for k, v in kwargs.items() if k in params}
    executor(page, step, **filtered)
