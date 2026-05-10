"""select_date_range step executor."""
from __future__ import annotations

from typing import Any

from open_banca_browser.errors import SelectorNotFound, StepTimeout, WidgetUnsupported
from open_banca_browser.step_executors._utils import extra
from open_banca_domain.entities.bank_map import StepSpec

_SUPPORTED_WIDGETS = {"ngb-datepicker", "html-date-input"}


def execute_select_date_range(page: Any, step: StepSpec) -> None:
    """Fill date range pickers for supported widget types.

    StepSpec extra fields:
        from_selector (str): CSS selector for the 'from' date input.
        to_selector (str): CSS selector for the 'to' date input.
        from_date (str): ISO-8601 date string, e.g. '2024-01-01'.
        to_date (str): ISO-8601 date string, e.g. '2024-01-31'.
        widget (str): Widget type. Supported: 'ngb-datepicker', 'html-date-input'.
                      Defaults to 'html-date-input'.

    Raises:
        WidgetUnsupported: Widget type is not in the supported list.
        SelectorNotFound: A date input selector is not found.
        StepTimeout: Interaction timed out.
    """
    params = extra(step)
    from_selector: str = params.get("from_selector", "")
    to_selector: str = params.get("to_selector", "")
    from_date: str = params.get("from_date", "")
    to_date: str = params.get("to_date", "")
    widget: str = params.get("widget", "html-date-input")

    if widget not in _SUPPORTED_WIDGETS:
        raise WidgetUnsupported(
            f"select_date_range: widget {widget!r} not supported. "
            f"Supported: {sorted(_SUPPORTED_WIDGETS)}"
        )

    try:
        if widget == "ngb-datepicker":
            _fill_ngb_datepicker(page, from_selector, from_date)
            _fill_ngb_datepicker(page, to_selector, to_date)
        else:
            # html-date-input: plain <input type="date"> — fill directly
            _fill_html_date_input(page, from_selector, from_date)
            _fill_html_date_input(page, to_selector, to_date)
    except (SelectorNotFound, StepTimeout, WidgetUnsupported):
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "timeout" in msg:
            raise StepTimeout(f"select_date_range timeout: {exc}") from exc
        raise StepTimeout(f"select_date_range error: {exc}") from exc


def _fill_html_date_input(page: Any, selector: str, date_str: str) -> None:
    """Fill a plain HTML date input."""
    locator = page.locator(selector)
    count = locator.count()
    if count == 0:
        raise SelectorNotFound(f"select_date_range: selector not found: {selector!r}")
    locator.fill(date_str, timeout=5_000)


def _fill_ngb_datepicker(page: Any, selector: str, date_str: str) -> None:
    """Fill an ng-bootstrap datepicker input by typing the date directly.

    ngb-datepicker typically accepts typed input in the bound <input> element.
    The date is typed after clearing the field, then blur fires validation.
    """
    locator = page.locator(selector)
    count = locator.count()
    if count == 0:
        raise SelectorNotFound(f"select_date_range: selector not found: {selector!r}")
    # Triple-click to select all, then type the date
    locator.triple_click(timeout=5_000)
    locator.type(date_str, timeout=5_000)
    # Trigger blur/change so the datepicker model updates
    locator.blur()
