"""fill step executor — resolves value_ref and fills an input, redacting sensitive data."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from open_banca_browser.errors import SelectorNotFound, StepTimeout, ValueNotResolved
from open_banca_browser.step_executors._utils import extra
from open_banca_domain.entities.bank_map import StepSpec

logger = logging.getLogger(__name__)


def execute_fill(
    page: Any,
    step: StepSpec,
    *,
    secret_resolver: Callable[[str], str],
) -> None:
    """Fill an input field with a resolved secret value.

    StepSpec extra fields:
        selector (str): CSS/XPath selector for the input element.
        value_ref (str): Key to resolve via secret_resolver (never logged).
        sensitive (bool): If True, mark the field as data-sensitive before fill
                          so that the screenshot redactor blanks it.

    Raises:
        ValueNotResolved: value_ref could not be resolved.
        SelectorNotFound: Input element not found in the DOM.
        StepTimeout: Element found but fill timed out.
    """
    params = extra(step)
    selector: str = params.get("selector", "")
    value_ref: str = params.get("value_ref", "")
    sensitive: bool = params.get("sensitive", False)

    # Resolve secret — never log the plaintext
    try:
        plaintext = secret_resolver(value_ref)
    except Exception as exc:
        raise ValueNotResolved(
            f"fill: could not resolve value_ref={value_ref!r}"
        ) from exc

    if not plaintext:
        raise ValueNotResolved(f"fill: empty value for value_ref={value_ref!r}")

    # Mark field as sensitive so screenshot redactor blanks it
    if sensitive:
        try:
            page.locator(selector).evaluate(
                "el => el.setAttribute('data-sensitive', 'true')"
            )
        except Exception:
            logger.debug("Could not mark field as sensitive; proceeding anyway")

    try:
        locator = page.locator(selector)
        count = locator.count()
        if count == 0:
            raise SelectorNotFound(f"fill: selector not found: {selector!r}")
        locator.fill(plaintext, timeout=10_000)
    except SelectorNotFound:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg:
            raise StepTimeout(f"fill timeout on {selector!r}") from exc
        if "not found" in msg or "no element" in msg:
            raise SelectorNotFound(f"fill: selector not found: {selector!r}") from exc
        raise StepTimeout(f"fill error on {selector!r}: {exc}") from exc
    finally:
        # Explicitly del plaintext to reduce time in memory
        del plaintext
